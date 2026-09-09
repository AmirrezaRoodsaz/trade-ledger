"""The runner against a real app and a fake venue.

The app is the in-process one from the `client` fixture: `BotClient` is handed
that `TestClient` as its httpx client, so every push, every journal call and
every guard is the real thing. The exchange is a fake that records **every**
write — which is how "the app was unreachable, so nothing was ordered" is a
test rather than a hope.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from trade_ledger.botkit.client import AppUnreachable, BotClient
from trade_ledger.botkit.runner import ReconciliationError, run_once, size
from trade_ledger.journal import ManualFill, open_trade, plan_trade
from trade_ledger.models import BotCommand, BotRun, BotState, Preset, PresetVersion, Trade
from trade_ledger.prices.service import Candle

PARAMS = {"entry": 5, "exit": 3, "atr_len": 3, "atr_mult": 2}
START = datetime(2026, 9, 1, tzinfo=UTC)
NOW = datetime(2026, 9, 9, 12, 5, tzinfo=UTC)


# -- fakes --------------------------------------------------------------------


class FakeExchange:
    """Scripted candles and balances; every write appended to `writes`."""

    def __init__(self, candles, *, market_type="spot", balances=None, positions=None):
        self.market_type = market_type
        self.writes: list[tuple] = []
        self._candles = candles
        self._balances = dict(balances or {"EUR": Decimal(2500)})
        self._positions = list(positions or [])
        self._open_orders: list[dict] = []

    # reads
    def balances(self):
        return dict(self._balances)

    def positions(self):
        return list(self._positions)

    def open_orders(self):
        return list(self._open_orders)

    def market_limits(self, symbol):
        return {"min_qty": Decimal("0.001"), "step": Decimal("0.001"), "tick": Decimal("0.01")}

    def candles(self, symbol, timeframe, limit):
        return self._candles.get(symbol, [])[-limit:]

    # writes
    def _move(self, symbol, side, qty):
        base = symbol.partition("/")[0]
        held = self._balances.get(base, Decimal(0))
        self._balances[base] = held + qty if side == "buy" else held - qty

    def place_order(self, symbol, side, qty, client_id):
        self.writes.append(("place_order", symbol, side, qty, client_id))
        self._move(symbol, side, qty)
        return {
            "kind": "order",
            "action": "place_order",
            "symbol": symbol,
            "side": side,
            "qty": str(qty),
            "client_id": client_id,
            "order_id": f"o{len(self.writes)}",
            "status": "closed",
            "filled": str(qty),
            "average": "120",
        }

    def place_stop(self, symbol, side, qty, stop_price, client_id):
        self.writes.append(("place_stop", symbol, side, qty, stop_price, client_id))
        stop_id = f"{client_id[:29]}sl"
        self._open_orders.append(
            {"symbol": symbol, "clientOrderId": stop_id, "stopLossPrice": float(stop_price)}
        )
        return {
            "kind": "order",
            "action": "place_stop",
            "symbol": symbol,
            "client_id": stop_id,
            "status": "open",
            "filled": None,
            "average": None,
        }

    def cancel_all(self, symbol):
        self.writes.append(("cancel_all", symbol))
        self._open_orders = [o for o in self._open_orders if o["symbol"] != symbol]

    def close_position(self, symbol):
        self.writes.append(("close_position", symbol))
        self._balances[symbol.partition("/")[0]] = Decimal(0)
        return {"kind": "order", "action": "close_position", "symbol": symbol, "status": "closed"}


def bars(count: int, *, level=Decimal(100), last: Decimal | None = None) -> list[Candle]:
    """A flat 4-hour series whose final bar closes at `last`."""
    out = [
        Candle(
            date=START + timedelta(hours=4 * i),
            open=level,
            high=level + 1,
            low=level - 1,
            close=level,
        )
        for i in range(count - 1)
    ]
    close = level if last is None else last
    out.append(
        Candle(
            date=START + timedelta(hours=4 * (count - 1)),
            open=level,
            high=max(close, level + 1),
            low=min(close, level - 1),
            close=close,
        )
    )
    return out


# -- app-side setup -----------------------------------------------------------


def _bot_with_preset(session, bot_factory, pairs, *, params=None, **bot_kwargs):
    bot, token = bot_factory(**bot_kwargs)
    preset = Preset(name=f"p-{bot.slug}", strategy="donchian")
    session.add(preset)
    session.flush()
    version = PresetVersion(
        preset_id=preset.id,
        version=1,
        params_json=json.dumps(params or PARAMS),
        timeframe="4h",
        pairs_json=json.dumps(pairs),
        risk_pct=Decimal(3),
        max_position_pct=Decimal(33),
        leverage_cap=Decimal(1),
    )
    session.add(version)
    session.flush()
    bot.preset_version_id = version.id
    session.commit()
    return bot, token


def _bot_client(test_client, token, slug) -> BotClient:
    return BotClient("http://testserver", token, slug, client=test_client)


def _seed_open_trade(session, bot, instrument, qty: Decimal, price=Decimal(100)) -> Trade:
    """A real open trade of this bot's, fills and all."""
    trade = plan_trade(
        session,
        {
            "account_id": bot.account_id,
            "instrument_id": instrument.id,
            "direction": "long",
            "planned_entry": price,
            "planned_stop": price * Decimal("0.9"),
            "risk_eur": Decimal(50),
            "planned_qty": qty,
            "note_pre": "seeded",
            "bot_id": bot.id,
        },
    )
    return open_trade(
        session,
        trade,
        manual=ManualFill(ts=START, quantity=qty, price=price, fee_eur=Decimal(0)),
    )


def _last_run(session, bot) -> BotRun:
    runs = [r for r in session.query(BotRun).all() if r.bot_id == bot.id]
    return max(runs, key=lambda r: r.id)


def _state(session, bot) -> BotState:
    return next(s for s in session.query(BotState).all() if s.bot_id == bot.id)


# -- the cases ----------------------------------------------------------------


def test_a_dry_run_plans_the_trade_and_sends_nothing_to_the_venue(
    client, session, bot_factory, instrument_factory
):
    instrument_factory(symbol="BTC/EUR")
    bot, token = _bot_with_preset(session, bot_factory, ["BTC/EUR"])
    exchange = FakeExchange({"BTC/EUR": bars(20, last=Decimal(120))})

    summary = run_once(_bot_client(client, token, bot.slug), exchange, dry_run=True, now=NOW)

    assert summary["status"] == "dry_run"
    assert summary["entries"] == 1
    assert exchange.writes == []

    trade = session.query(Trade).one()
    assert (trade.status, trade.bot_id) == ("planned", bot.id)
    assert trade.planned_qty > 0
    assert trade.planned_stop < trade.planned_entry
    assert _last_run(session, bot).status == "dry_run"


def test_the_demo_path_orders_stops_opens_the_trade_and_pushes_the_stop(
    client, session, bot_factory, instrument_factory
):
    instrument_factory(symbol="BTC/EUR")
    bot, token = _bot_with_preset(session, bot_factory, ["BTC/EUR"])
    exchange = FakeExchange({"BTC/EUR": bars(20, last=Decimal(120))})

    summary = run_once(_bot_client(client, token, bot.slug), exchange, now=NOW)

    assert summary["status"] == "ok"
    trade = session.query(Trade).one()
    assert trade.status == "open"

    entry, stop = exchange.writes
    # The order carries the journal id, so a fill traces back to the plan.
    assert entry == ("place_order", "BTC/EUR", "buy", trade.planned_qty, trade.external_ref)
    assert stop[:2] == ("place_stop", "BTC/EUR")
    assert stop[5] == trade.external_ref

    state = _state(session, bot)
    positions = json.loads(state.positions_json)
    assert state.reconciliation == "ok"
    assert [(p["symbol"], p["stop_present"]) for p in positions] == [("BTC/EUR", True)]
    assert positions[0]["stop_price"] == str(float(trade.planned_stop))


def test_a_reconciliation_mismatch_flattens_and_fails_the_run(
    client, session, bot_factory, instrument_factory
):
    instrument = instrument_factory(symbol="BTC/EUR")
    bot, token = _bot_with_preset(
        session, bot_factory, ["BTC/EUR"], params=PARAMS | {"on_mismatch": "flat"}
    )
    _seed_open_trade(session, bot, instrument, Decimal(2))
    # The journal says two coins, the venue says none.
    exchange = FakeExchange({"BTC/EUR": bars(20)}, balances={"EUR": Decimal(2500)})

    with pytest.raises(ReconciliationError, match="journal 2, exchange 0"):
        run_once(_bot_client(client, token, bot.slug), exchange, now=NOW)

    assert exchange.writes == [("cancel_all", "BTC/EUR"), ("close_position", "BTC/EUR")]
    run = _last_run(session, bot)
    assert run.status == "error"
    assert "BTC/EUR" in run.error
    assert _state(session, bot).reconciliation == "mismatch"


def test_a_mismatch_under_the_halt_policy_touches_nothing(
    client, session, bot_factory, instrument_factory
):
    instrument = instrument_factory(symbol="BTC/EUR")
    bot, token = _bot_with_preset(session, bot_factory, ["BTC/EUR"])  # default policy
    _seed_open_trade(session, bot, instrument, Decimal(2))
    exchange = FakeExchange({"BTC/EUR": bars(20)}, balances={"EUR": Decimal(2500)})

    with pytest.raises(ReconciliationError):
        run_once(_bot_client(client, token, bot.slug), exchange, now=NOW)

    assert exchange.writes == []
    assert _last_run(session, bot).status == "error"


def test_an_unreachable_app_never_reaches_the_venue(client, session, bot_factory):
    _bot_with_preset(session, bot_factory, ["BTC/EUR"], name="ghosted")
    exchange = FakeExchange({"BTC/EUR": bars(20, last=Decimal(120))})

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    offline = BotClient(
        "http://app.test",
        "tok",
        "ghosted",
        client=httpx.Client(transport=httpx.MockTransport(refuse)),
    )

    with pytest.raises(AppUnreachable):
        run_once(offline, exchange, now=NOW)

    assert exchange.writes == []


def test_a_pause_command_skips_entries_but_still_exits(
    client, session, bot_factory, instrument_factory
):
    btc = instrument_factory(symbol="BTC/EUR")
    instrument_factory(symbol="ETH/EUR")
    bot, token = _bot_with_preset(session, bot_factory, ["BTC/EUR", "ETH/EUR"])
    held = _seed_open_trade(session, bot, btc, Decimal(2))
    session.add(BotCommand(bot_id=bot.id, kind="pause", reason="hands off", issued_by="ui"))
    session.commit()

    exchange = FakeExchange(
        {
            "BTC/EUR": bars(20, last=Decimal(80)),  # breaks the 3-bar low -> exit
            "ETH/EUR": bars(20, last=Decimal(120)),  # breaks the 5-bar high -> entry
        },
        balances={"EUR": Decimal(2500), "BTC": Decimal(2)},
    )

    summary = run_once(_bot_client(client, token, bot.slug), exchange, now=NOW)

    assert (summary["exits"], summary["entries"]) == (1, 0)
    assert [w[0] for w in exchange.writes] == ["cancel_all", "place_order"]
    assert exchange.writes[1][1:3] == ("BTC/EUR", "sell")
    session.refresh(held)
    assert held.status == "closed"
    # No second trade: the entry was skipped, not planned.
    assert session.query(Trade).count() == 1

    command = session.query(BotCommand).one()
    assert (command.result, bot.paused_entries) == ("ok", True)


def test_a_re_run_over_the_same_bar_reuses_the_planned_trade(
    client, session, bot_factory, instrument_factory
):
    instrument_factory(symbol="BTC/EUR")
    bot, token = _bot_with_preset(session, bot_factory, ["BTC/EUR"])
    exchange = FakeExchange({"BTC/EUR": bars(20, last=Decimal(120))})
    bot_client = _bot_client(client, token, bot.slug)

    first = run_once(bot_client, exchange, dry_run=True, now=NOW)
    second = run_once(bot_client, exchange, dry_run=True, now=NOW)

    assert (first["entries"], second["entries"]) == (1, 1)
    assert session.query(Trade).count() == 1
    assert exchange.writes == []


def test_a_disabled_bot_finishes_skipped_without_looking_at_the_venue(
    client, session, bot_factory, instrument_factory
):
    instrument_factory(symbol="BTC/EUR")
    bot, token = _bot_with_preset(session, bot_factory, ["BTC/EUR"], enabled=False)
    exchange = FakeExchange({"BTC/EUR": bars(20, last=Decimal(120))})

    summary = run_once(_bot_client(client, token, bot.slug), exchange, now=NOW)

    assert summary["status"] == "skipped"
    assert exchange.writes == []
    assert session.query(Trade).count() == 0
    assert _last_run(session, bot).status == "skipped"


# -- sizing -------------------------------------------------------------------


def _size(**overrides):
    args = {
        "equity": Decimal(2500),
        "risk_pct": Decimal(3),
        "max_position_pct": Decimal(33),
        "leverage_cap": Decimal(1),
        "limits": {"min_qty": Decimal("0.001"), "step": Decimal("0.001")},
    }
    args.update(overrides)
    return size(Decimal(100), Decimal(90), **args)


def test_size_is_the_risk_over_the_stop_distance_rounded_to_the_step():
    # 3 % of 2.500 = 75 EUR over a 10-point stop = 7,5 units.
    assert _size()[0] == Decimal("7.5")


def test_the_position_cap_wins_when_it_is_the_smaller_number():
    # 33 % of 2.500 at 100 = 8,25 units, so the risk size still fits...
    assert _size()[0] == Decimal("7.5")
    # ...but at 5 % it does not: 125 EUR of exposure = 1,25 units.
    assert _size(max_position_pct=Decimal(5))[0] == Decimal("1.25")


def test_leverage_caps_the_exposure_too():
    # No leverage: 2.500 EUR of equity buys 25 units at 100, whatever the risk.
    assert _size(risk_pct=Decimal(200), max_position_pct=None)[0] == Decimal(25)
    assert _size(risk_pct=Decimal(200), max_position_pct=None, leverage_cap=Decimal(2))[0] == (
        Decimal(50)
    )


def test_a_size_below_the_venue_minimum_is_refused_rather_than_rounded_up():
    qty, why = _size(equity=Decimal(1), limits={"min_qty": Decimal(1), "step": Decimal("0.001")})
    assert qty is None
    assert "below the venue minimum 1" in why


def test_a_stop_at_the_entry_is_not_a_trade():
    qty, why = size(
        Decimal(100),
        Decimal(100),
        equity=Decimal(2500),
        risk_pct=Decimal(3),
        max_position_pct=None,
        leverage_cap=Decimal(1),
        limits={},
    )
    assert (qty, why) == (None, "stop equals entry")


def test_an_infeasible_entry_is_skipped_with_a_warning_event(
    client, session, bot_factory, instrument_factory
):
    instrument_factory(symbol="BTC/EUR")
    # 0,0001 % of 2.500 EUR is 0,0025 EUR of risk — far under the lot step.
    bot, token = _bot_with_preset(session, bot_factory, ["BTC/EUR"])
    version = session.query(PresetVersion).one()
    version.risk_pct = Decimal("0.0001")
    version.max_position_pct = None
    session.commit()
    exchange = FakeExchange({"BTC/EUR": bars(20, last=Decimal(120))})

    summary = run_once(_bot_client(client, token, bot.slug), exchange, now=NOW)

    assert summary["entries"] == 0
    assert summary["skipped"] == ["BTC/EUR: size 0.000 below the venue minimum 0.001"]
    assert exchange.writes == []
    assert session.query(Trade).count() == 0
