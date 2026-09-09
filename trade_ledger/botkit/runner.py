"""One pass of the lab plan, in the order the plan fixes:

config -> heartbeat -> start run -> reconcile -> commands -> candles ->
signals -> exits -> sizing/feasibility -> intents -> orders -> stops ->
state -> finish run.

Two interlocks decide everything else in here:

* **The app authorises the order.** Every entry is journalled first; the
  trade's `external_ref` becomes the exchange `clientOrderId`. If the app is
  unreachable at any point before an order is sent, `AppUnreachable` comes
  out of the client and nothing is ever sent to the venue.
* **An order is sent once.** After a write reaches the exchange a failing
  app call is logged to stderr and the run carries on to push state; it is
  never a reason to re-send. What the app then misses, reconciliation on the
  next run finds.

Reconciliation compares the app's open trades with what the venue actually
holds. On a mismatch the bot does not trade: it either flattens everything
(`on_mismatch: "flat"` in the preset params) or halts (`"halt"`, the
default), and raises `ReconciliationError` either way.
"""

from __future__ import annotations

import hashlib
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal
from importlib import metadata

from .strategies import DEFAULT_PARAMS, STRATEGIES

# Relative tolerance for "the journal and the venue agree". Fills come back
# rounded to the lot step, so an exact match is not a thing that happens.
QTY_TOLERANCE = Decimal("0.01")

# Bars fetched on top of the longest window a strategy asks for — ATR needs
# one extra bar to seed, the channel excludes the current one.
CANDLE_HEADROOM = 10


class ReconciliationError(Exception):
    """The journal and the venue disagree. The run stops without trading."""


def code_version() -> str:
    try:
        return metadata.version("trade-ledger")
    except metadata.PackageNotFoundError:  # pragma: no cover - editable installs only
        return "unknown"


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _base_ccy(symbol: str) -> str:
    return symbol.partition("/")[0]


def _quote_ccy(symbol: str) -> str:
    return symbol.partition("/")[2].partition(":")[0]


def _event(kind: str, message: str, **payload) -> dict:
    return {"kind": kind, "message": message, "payload": payload}


def _stderr(message: str) -> None:
    print(message, file=sys.stderr)


def _safe(what: str, fn, *args, **kwargs):
    """A call made *after* an order was sent. The venue already has the
    position; losing the journal entry is bad but re-sending the order is
    worse, so **any** failure here is logged and the run carries on. A 409
    from a lifecycle guard is as unrecoverable as an unreachable app, and
    neither is a reason to touch the venue again.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - deliberately everything; see the docstring
        _stderr(f"{what} failed after the order was sent: {type(exc).__name__}: {exc}")
        return None


def intent_ref(slug: str, symbol: str, bar_ts: datetime) -> str:
    """The journal id (and so the `clientOrderId`) for one signal.

    Derived from bot, symbol and the bar that produced the signal, so a
    second run over the same bar computes the same ref, finds the trade it
    already planned and does not file a second one. Alphanumeric and 24
    characters, inside every venue's `clientOrderId` budget.
    """
    digest = hashlib.sha256(f"{slug}|{symbol}|{bar_ts.isoformat()}".encode()).hexdigest()
    return f"{re.sub(r'[^A-Za-z0-9]', '', slug)[:12]}{digest[:12]}"


def floor_to(value: Decimal, step: Decimal | None) -> Decimal:
    """`value` rounded **down** to a multiple of `step`.

    Down in both uses: a size that rounds up is a size the venue rejects, and
    a long stop that rounds up sits closer to the entry than the strategy
    asked for.
    """
    return (value // step) * step if step else value


def exit_ref(entry_ref: str) -> str:
    """The exit order's own client id. Two live orders may not share one, and
    the entry's ref is already spoken for by the entry and (with `sl`) its
    stop.
    """
    return f"{entry_ref[:31]}x"


# -- reconciliation -----------------------------------------------------------


def journal_positions(client) -> tuple[dict[str, Decimal], dict[str, dict]]:
    """`({symbol: qty}, {symbol: trade})` from the app's open trades.

    A symbol traded twice is summed for the quantity; the trade kept is the
    first one, which is the one an exit closes.
    """
    symbols = {row["id"]: row["symbol"] for row in client.instruments()}
    quantities: dict[str, Decimal] = {}
    trades: dict[str, dict] = {}
    for trade in client.open_trades():
        symbol = symbols.get(trade["instrument_id"])
        if symbol is None:  # pragma: no cover - an instrument deleted mid-flight
            continue
        qty = _dec(trade.get("quantity") or trade.get("planned_qty") or 0)
        quantities[symbol] = quantities.get(symbol, Decimal(0)) + qty
        trades.setdefault(symbol, trade)
    return quantities, trades


def live_positions(exchange, pairs: list[str]) -> dict[str, dict]:
    """What the venue holds, keyed by symbol.

    Spot has no position concept, so exposure is read off the balances: the
    base currency of each configured pair, counted as held once it reaches
    the market's minimum order size. Dust below that cannot be sold anyway.
    # ponytail: a spot balance the operator bought by hand looks exactly like
    # one the bot opened. `bots/README.md` says to give a bot its own
    # sub-account for that reason.
    """
    if exchange.market_type == "spot":
        balances = exchange.balances()
        out = {}
        for symbol in pairs:
            qty = _dec(balances.get(_base_ccy(symbol), 0))
            floor = exchange.market_limits(symbol).get("min_qty") or Decimal(0)
            if qty > 0 and qty >= floor:
                out[symbol] = {"qty": qty, "avg_entry": None, "unrealised_quote": None}
        return out
    return {
        position["symbol"]: {
            "qty": abs(position["qty"]),
            "avg_entry": position.get("avg_entry"),
            "unrealised_quote": position.get("unrealised_quote"),
        }
        for position in exchange.positions()
    }


def mismatch(journal: dict[str, Decimal], live: dict[str, dict]) -> str | None:
    """A human-readable list of the disagreements, or `None`."""
    problems = []
    for symbol in sorted(set(journal) | set(live)):
        booked = journal.get(symbol, Decimal(0))
        held = live.get(symbol, {}).get("qty", Decimal(0))
        biggest = max(abs(booked), abs(held))
        if biggest and abs(booked - held) > biggest * QTY_TOLERANCE:
            problems.append(f"{symbol}: journal {booked}, exchange {held}")
    return "; ".join(problems) or None


# -- sizing -------------------------------------------------------------------


def size(
    entry: Decimal,
    stop: Decimal,
    *,
    equity: Decimal,
    risk_pct: Decimal,
    max_position_pct: Decimal | None,
    leverage_cap: Decimal,
    limits: dict,
) -> tuple[Decimal | None, str]:
    """`(qty, why)`. `qty` is `None` when the trade is not feasible.

    Risk first, caps second: `risk / stop distance`, then trimmed by the
    position-size cap and the leverage cap, then rounded *down* to the lot
    step. `why` carries the reason a `None` was returned, and otherwise the
    money actually at risk once the caps have had their say — which is the
    number the journal grades R against.

    # ponytail: `risk_eur` is EUR and the stop distance is in the market's
    # quote currency; on a USDT pair this reads 1 USDT = 1 EUR. FX conversion
    # lives in the app, not in a bot process. Ceiling: sizes are off by the
    # EUR/USD rate until the app hands the bot a converted equity.
    """
    distance = abs(entry - stop)
    if distance <= 0:
        return None, "stop equals entry"
    if entry <= 0:
        return None, f"entry price {entry} is not positive"

    qty = equity * risk_pct / 100 / distance
    caps = [leverage_cap * equity / entry]
    # A `max_position_pct` of 0 (or None) means *no cap*, not "no position" —
    # a preset that leaves the field empty must still be tradable.
    if max_position_pct:
        caps.append(max_position_pct / 100 * equity / entry)
    qty = min([qty, *caps])

    qty = floor_to(qty, limits.get("step"))
    floor = limits.get("min_qty") or Decimal(0)
    if qty <= 0 or qty < floor:
        return None, f"size {qty} below the venue minimum {floor}"
    return qty, f"risk {qty * distance} over a {distance} stop"


# -- the run ------------------------------------------------------------------


def run_once(client, exchange, *, dry_run: bool = False, now: datetime | None = None) -> dict:
    """One pass. Returns the run summary; also pushed to the app.

    Raises `AppUnreachable` when the app cannot be reached (before any order
    is sent) and `ReconciliationError` when the journal and the venue
    disagree.
    """
    now = now or datetime.now(UTC)
    config = client.get_config()
    bot = config["bot"]
    dry_run = bool(dry_run or bot["dry_run"])

    client.heartbeat(None, code_version())
    run_id = client.start_run()

    events: list[dict] = []
    summary: dict = {
        "dry_run": dry_run,
        "reconciliation": "unknown",
        "entries": 0,
        "exits": 0,
        "orders": 0,
        "skipped": [],
    }

    if not bot["enabled"]:
        summary["status"] = "skipped"
        client.finish_run(run_id, "skipped", summary)
        return summary

    try:
        summary["status"] = _trade(client, exchange, config, dry_run, now, events, summary)
    except Exception as exc:
        summary["status"] = "error"
        # The venue was touched, so the app's picture of it is out of date and
        # a kill rule may need to see the truth. Best effort — this is already
        # the failure path.
        if summary.get("venue_touched") and config["preset"] is not None:
            _safe(
                "push state",
                _push_state,
                client,
                exchange,
                config,
                config["preset"]["pairs"],
                summary["reconciliation"],
                f"run failed: {exc}",
                events,
            )
        _safe("push events", client.push_events, events)
        _safe(
            "finish run",
            client.finish_run,
            run_id,
            "error",
            summary,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

    client.push_events(events)
    client.finish_run(run_id, summary["status"], summary)
    return summary


def _trade(client, exchange, config, dry_run: bool, now, events: list, summary: dict) -> str:
    bot, preset = config["bot"], config["preset"]
    finished = "dry_run" if dry_run else "ok"

    if preset is None:
        events.append(_event("warning", "no preset assigned: nothing to trade"))
        return "skipped"
    strategy = STRATEGIES.get(preset["strategy"])
    if strategy is None:
        raise ValueError(f"unknown strategy: {preset['strategy']}")

    pairs: list[str] = preset["pairs"]
    params: dict = preset["params"]
    limits = {symbol: exchange.market_limits(symbol) for symbol in pairs}

    # -- reconcile ------------------------------------------------------------
    booked, open_trades = journal_positions(client)
    live = live_positions(exchange, pairs)
    detail = mismatch(booked, live)
    if detail is not None:
        summary["reconciliation"] = "mismatch"
        policy = str(params.get("on_mismatch", "halt"))
        summary["on_mismatch"] = policy
        events.append(_event("reconcile", f"mismatch: {detail}", policy=policy))
        if policy == "flat" and not dry_run:
            summary["flattened"] = len(
                _flatten(exchange, sorted(set(booked) | set(live)), events, summary)
            )
        # State goes up on either policy: the app's K4 kill rule reads
        # `BotState.reconciliation`, and a halt that pushes nothing leaves
        # the operator with no alert at all.
        # Best effort, both of them: the mismatch is what the caller has to
        # hear about, and a venue read failing on the way out must not
        # swallow it.
        _safe("push state", _push_state, client, exchange, config, pairs, "mismatch", detail,
              events)
        _safe("push events", client.push_events, events)
        events.clear()
        raise ReconciliationError(detail)
    summary["reconciliation"] = "ok"

    # -- commands -------------------------------------------------------------
    entries_blocked, flattened = _apply_commands(
        client, exchange, config, sorted(set(booked) | set(live)), dry_run, events, summary
    )
    if flattened is not None:
        # The venue is flat, so `booked`, `open_trades` and `live` are all
        # stale — trading on them would sell a position that no longer exists
        # (a naked short on a swap). Square the journal and stop here.
        _close_journal(client, exchange, preset, open_trades, flattened, now, events, summary)
        summary["flattened_by_command"] = True
        _push_state(client, exchange, config, pairs, "ok", "flattened by command", events)
        return finished
    if bot["paused_entries"]:
        entries_blocked = True

    # -- candles and signals --------------------------------------------------
    merged = DEFAULT_PARAMS.get(preset["strategy"], {}) | params
    bars = max(int(merged.get(k, 0)) for k in ("entry", "exit", "atr_len")) or 55
    candles = {
        symbol: exchange.candles(symbol, preset["timeframe"], bars + CANDLE_HEADROOM)
        for symbol in pairs
    }
    held = {symbol: {"qty": qty} for symbol, qty in booked.items()}
    signals = strategy(candles, params, held)
    summary["signals"] = [f"{s.side} {s.symbol}: {s.reason}" for s in signals]

    # -- exits before entries -------------------------------------------------
    for signal in [s for s in signals if s.side == "sell"]:
        _exit(
            client,
            exchange,
            signal,
            open_trades,
            booked,
            live,
            limits[signal.symbol],
            _last_close(candles[signal.symbol]),
            dry_run,
            now,
            events,
            summary,
        )

    # -- entries --------------------------------------------------------------
    equity, source = _sizing_base(exchange, bot, pairs)
    for signal in [s for s in signals if s.side == "buy"]:
        if entries_blocked:
            summary["skipped"].append(f"{signal.symbol}: entries paused")
            events.append(_event("info", f"entry skipped, entries paused: {signal.symbol}"))
            continue
        _enter(
            client,
            exchange,
            signal,
            bot=bot,
            preset=preset,
            equity=equity,
            equity_source=source,
            limits=limits[signal.symbol],
            candles=candles[signal.symbol],
            dry_run=dry_run,
            now=now,
            events=events,
            summary=summary,
        )

    _push_state(client, exchange, config, pairs, "ok", None, events)
    return finished


def _flatten(exchange, symbols, events: list, summary: dict) -> dict[str, dict]:
    """Cancel everything resting and close every position, symbol by symbol.
    Returns the close result per symbol, which carries the fill.

    Cancel first: on spot a resting sell order locks the very coins the close
    would sell.
    """
    closed = {}
    for symbol in sorted(symbols):
        summary["venue_touched"] = True
        exchange.cancel_all(symbol)
        events.append(_event("order", f"cancelled resting orders on {symbol}"))
        closed[symbol] = exchange.close_position(symbol)
        events.append(_event("order", f"closed {symbol}", **_order_payload(closed[symbol])))
    return closed


def _close_journal(client, exchange, preset, open_trades, flattened, now, events, summary) -> None:
    """Close the bot's open journal trades after a commanded flatten.

    The price is the flatten's own fill; when the venue reported none (it had
    nothing to close) one candle stands in, because a closed trade still has
    to carry a number the journal can grade.
    """
    for symbol, trade in sorted(open_trades.items()):
        order = flattened.get(symbol) or {}
        qty, price = _fill(order, _dec(trade.get("quantity") or 0), None)
        if price is None:
            price = _last_close(exchange.candles(symbol, preset["timeframe"], 1))
        summary["exits"] += 1
        _safe(
            "close trade after a commanded flatten",
            client.close_trade,
            trade["id"],
            ts=now,
            quantity=qty,
            price=price,
            fee_eur=Decimal(0),
        )
        events.append(_event("info", f"journal closed after flat: {symbol}", trade_id=trade["id"]))


def _order_payload(event: dict) -> dict:
    """An exchange event, trimmed to what an app-side `order` event carries."""
    return {k: v for k, v in event.items() if k != "kind"}


def _apply_commands(client, exchange, config, symbols, dry_run, events, summary):
    """Acknowledge every pending command.

    Returns `(entries_blocked, flattened)` — `flattened` is the close result
    per symbol when a `flat` command was carried out, and `None` otherwise.
    `flat` blocks entries for this run too: the app sets `paused_entries` on
    the ack, but that only reaches the bot with the *next* config.
    """
    blocked = False
    flattened = None
    acked = []
    for command in config["commands"]:
        kind, detail = command["kind"], ""
        if kind == "pause":
            blocked, detail = True, "entries skipped this run"
        elif kind == "flat":
            blocked = True
            if dry_run:
                detail = "dry run: nothing was sent to the venue"
            else:
                flattened = _flatten(exchange, symbols, events, summary)
                detail = f"flattened {len(flattened)} symbol(s)"
        elif kind not in ("resume", "run_now", "reload_config", "dry_run_on", "dry_run_off"):
            client.ack_command(command["id"], "error", f"unknown command: {kind}")
            events.append(_event("command", f"unknown command: {kind}"))
            continue
        client.ack_command(command["id"], "ok", detail)
        acked.append(kind)
    if acked:
        summary["commands"] = acked
    return blocked, flattened


def _last_close(candles) -> Decimal | None:
    return candles[-1].close if candles else None


def _fill(order: dict, fallback_qty: Decimal, fallback_price: Decimal | None):
    """`(qty, price)` out of an exchange order event, with the intended size
    and the signal's price standing in when the venue reports neither.
    """
    qty = _dec(order["filled"]) if order.get("filled") else fallback_qty
    price = _dec(order["average"]) if order.get("average") else fallback_price
    return qty, price


def _exit(
    client, exchange, signal, open_trades, booked, live, limits, last_close, dry_run, now,
    events, summary
) -> None:
    symbol = signal.symbol
    trade = open_trades.get(symbol)
    if trade is None:  # pragma: no cover - `held` is built from these very trades
        return
    # The smaller of what the journal thinks and what the venue holds, floored
    # to the lot step: inside the reconciliation tolerance the two still
    # differ, and an order for one tick more than the balance is rejected
    # outright — which would leave the position open with its exit unsent.
    held = live.get(symbol, {}).get("qty")
    qty = floor_to(min(booked[symbol], held) if held is not None else booked[symbol],
                   limits.get("step"))
    if qty <= 0:
        events.append(_event("warning", f"exit skipped, nothing sellable on {symbol}"))
        return
    if dry_run:
        summary["exits"] += 1
        events.append(_event("info", f"would exit {qty} {symbol}: {signal.reason}"))
        return

    summary["venue_touched"] = True
    exchange.cancel_all(symbol)
    order = exchange.place_order(symbol, "sell", qty, exit_ref(trade["external_ref"]))
    summary["exits"] += 1
    summary["orders"] += 1
    events.append(_event("order", f"exit {symbol}", **_order_payload(order)))

    filled, price = _fill(order, qty, last_close)
    _safe(
        "close trade",
        client.close_trade,
        trade["id"],
        ts=now,
        quantity=filled,
        price=price,
        # ponytail: the venue's fee is not on a ccxt market order reliably;
        # the app's transaction import carries the real number.
        fee_eur=Decimal(0),
    )


def _enter(
    client,
    exchange,
    signal,
    *,
    bot,
    preset,
    equity,
    equity_source,
    limits,
    candles,
    dry_run,
    now,
    events,
    summary,
) -> None:
    symbol = signal.symbol
    # Quantised once, here, so the size, the journalled plan and the resting
    # order all measure R against the same stop. Down, so the stop never ends
    # up tighter than the strategy asked for.
    stop_price = floor_to(signal.stop, limits.get("tick"))
    qty, why = size(
        signal.entry,
        stop_price,
        equity=equity,
        risk_pct=_dec(preset["risk_pct"] or 0),
        max_position_pct=_dec(preset["max_position_pct"]) if preset["max_position_pct"] else None,
        leverage_cap=_dec(preset["leverage_cap"] or 1),
        limits=limits,
    )
    if qty is None:
        summary["skipped"].append(f"{symbol}: {why}")
        events.append(_event("warning", f"entry not feasible on {symbol}: {why}"))
        return

    ref = intent_ref(bot["slug"], symbol, candles[-1].date)
    trade = client.find_trade(ref)
    if trade is not None and trade["status"] != "planned":
        events.append(
            _event("info", f"entry already executed on {symbol}", trade_id=trade["id"], ref=ref)
        )
        return
    if trade is None:
        trade = client.plan_trade(
            instrument_symbol=symbol,
            asset_class=_asset_class(exchange),
            # ponytail: long only, because the one strategy in the registry
            # is. A short signal would need `side` on `Signal` and a sell
            # entry here; add it with the strategy that needs it.
            direction="long",
            entry=signal.entry,
            stop=stop_price,
            risk_eur=qty * abs(signal.entry - stop_price),
            qty=qty,
            reason=f"{signal.reason} [{equity_source}, {why}]",
            external_ref=ref,
        )

    summary["entries"] += 1
    if dry_run:
        events.append(_event("info", f"would enter {qty} {symbol}", trade_id=trade["id"], ref=ref))
        return

    summary["venue_touched"] = True
    order = exchange.place_order(symbol, "buy", qty, trade["external_ref"])
    summary["orders"] += 1
    events.append(_event("order", f"entry {symbol}", **_order_payload(order)))
    filled, price = _fill(order, qty, _last_close(candles) or signal.entry)

    # The stop goes on before the journal entry: the position is live from
    # the moment the order returns, and an unprotected position is the one
    # thing this bot may never leave behind. If the stop cannot be placed,
    # the position is closed again rather than left naked, and nothing is
    # journalled — the plan stays `planned`, the events say what happened.
    # ponytail: that plan keeps its ref, so a re-run inside the same bar
    # would try the entry again. Cancel it through `/api/trades/{id}/cancel`
    # once the client speaks that route; until then the 4-hour bar is the
    # only thing between this and a retry loop.
    try:
        stop = exchange.place_stop(symbol, "sell", filled, stop_price, trade["external_ref"])
    except Exception as exc:  # noqa: BLE001 - any refusal leaves the position naked
        events.append(_event("error", f"stop rejected on {symbol}: {exc}", trade_id=trade["id"]))
        summary["skipped"].append(f"{symbol}: stop rejected, position closed again")
        closed = exchange.close_position(symbol)
        events.append(_event("order", f"closed unprotected {symbol}", **_order_payload(closed)))
        return
    events.append(_event("order", f"stop {symbol}", **_order_payload(stop)))

    _safe(
        "open trade",
        client.open_trade,
        trade["id"],
        ts=now,
        quantity=filled,
        price=price,
        fee_eur=Decimal(0),
    )


def _asset_class(exchange) -> str:
    return "crypto" if exchange.market_type == "spot" else "perp"


def _sizing_base(exchange, bot, pairs) -> tuple[Decimal, str]:
    """`(equity, where it came from)` for position sizing.

    The venue reports balances per currency. When every pair settles in EUR
    that free balance *is* the equity; anything else would need an FX rate,
    which lives in the app, so the bot falls back on the stage capital the
    app told it about.
    """
    stage = _dec(bot["stage_capital_eur"])
    if {_quote_ccy(symbol) for symbol in pairs} == {"EUR"}:
        balance = exchange.balances().get("EUR")
        if balance is not None:
            return _dec(balance), "EUR balance"
    return stage, "stage capital (quote is not EUR; FX conversion lives in the app)"


def _stop_prices(open_orders: list[dict]) -> dict[str, str]:
    """`{symbol: stop price}` from the resting orders.

    A stop is recognised either by its trigger price or by the `sl` suffix
    this botkit puts on the client id — venues report the price under three
    different keys and some report none at all.
    """
    out = {}
    for order in open_orders:
        info = order.get("info") or {}
        price = (
            order.get("stopLossPrice")
            or order.get("triggerPrice")
            or order.get("stopPrice")
            or info.get("slTriggerPx")
        )
        if price or str(order.get("clientOrderId") or "").endswith("sl"):
            out[order.get("symbol")] = None if price is None else str(price)
    return out


def _push_state(client, exchange, config, pairs, reconciliation, detail, events) -> None:
    """Positions, orders, equity and the reconciliation verdict, read fresh
    from the venue so the app sees the world as it is after this run.
    """
    bot, preset = config["bot"], config["preset"]
    open_orders = exchange.open_orders()
    stops = _stop_prices(open_orders)
    positions = [
        {
            "symbol": symbol,
            "qty": str(position["qty"]),
            "avg_entry": None if position["avg_entry"] is None else str(position["avg_entry"]),
            "stop_present": symbol in stops,
            "stop_price": stops.get(symbol),
            "unrealised_quote": (
                None
                if position["unrealised_quote"] is None
                else str(position["unrealised_quote"])
            ),
        }
        for symbol, position in sorted(live_positions(exchange, pairs).items())
    ]
    balances = exchange.balances()
    equity, source = _sizing_base(exchange, bot, pairs)
    client.push_state(
        equity_eur=equity if source == "EUR balance" else None,
        positions=positions,
        open_orders=[{k: v for k, v in o.items() if k != "info"} for o in open_orders],
        reconciliation=reconciliation,
        reconciliation_detail=detail,
        # The preset version the run actually used — unique app-side, so the
        # operator can tell which config produced which state.
        config_version=None if preset is None else preset["version_id"],
        extra={
            "balances": {ccy: str(amount) for ccy, amount in balances.items() if amount},
            "sizing_base_eur": str(equity),
            "sizing_base_source": source,
            "market_type": exchange.market_type,
        },
    )
    events.append(_event("info", f"state pushed: {len(positions)} position(s)"))
