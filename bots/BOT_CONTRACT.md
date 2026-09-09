# Bot contract

What a trade-ledger bot must do, in the order it must do it, with the exact
JSON the app expects. Hand this file to whoever (or whatever) writes the next
bot; it is self-contained and generated from the code it describes:
`trade_ledger/api/bot_push.py`, `api/trades.py`, `api/backtests.py`,
`trade_ledger/botkit/{client,runner,run}.py`,
`trade_ledger/botkit/strategies/`, `trade_ledger/bots/{kill_rules,commands}.py`.

Reference implementation: `trade_ledger/botkit/`. A bot in another language
is fine as long as it keeps this contract.

---

## 1. Purpose and the three hard rules

A bot is a **separate process**. It reads its configuration from the app,
talks to exactly one exchange, and pushes back what it did. The app is the
book of record and the operator's console; it never places an order.

1. **The app never holds trade keys.** Exchange credentials live only in the
   bot's env file (`data/bots/<slug>/.env`, or `/etc/trade-bot/<slug>.env`
   under systemd). The app process must never import
   `trade_ledger.botkit.exchange` — that module is the only place in the repo
   with an exchange write call.
2. **Journal first.** Every entry is planned in the app *before* the order
   exists (`POST /api/trades`), and the returned `external_ref` is the
   order's `clientOrderId`. A fill can always be traced back to the plan that
   authorised it.
3. **App unreachable → no orders.** If any call the bot needs before an order
   fails, the bot raises and stops. No app, no journal entry, therefore no
   order. Nothing is ever sent to the venue "and journalled later".

---

## 2. The loop, in order

One run = one pass. A scheduler (the app's supervisor, or a systemd timer)
decides when. `run_once()` in `botkit/runner.py` is this list.

| # | Step | Call |
|---|---|---|
| 1 | Fetch config | `GET /api/bots/{slug}/config` |
| 2 | Heartbeat | `POST /api/bots/{slug}/heartbeat` |
| 3 | Start the run | `POST /api/bots/{slug}/runs` → `run_id` |
| 4 | `enabled == false`? finish `skipped`, touch nothing | `PATCH …/runs/{run_id}` |
| 5 | `preset == null`? file a warning event, finish `skipped` | `PATCH …/runs/{run_id}` |
| 6 | Venue limits per pair (min size, lot step, tick) | `market_limits(symbol)` |
| 7 | Read the journal side | `GET /api/instruments`, `GET /api/trades?status=open&mode=all` |
| 8 | Read the venue side | `balances()` (spot) or `positions()` (swap) |
| 9 | Reconcile the two | local comparison, 1 % relative tolerance per symbol |
| 10 | Mismatch → apply `on_mismatch`, push state + events, fail the run | `POST …/state`, `POST …/events` |
| 11 | Acknowledge pending commands, oldest first | `POST …/commands/{id}/ack` |
| 12 | `flat` command → cancel + close everything, close the journal trades, push state, end the run | `cancel_all()`, `close_position()`, `POST /api/trades/{id}/close` |
| 13 | Fetch completed candles per pair | `candles(symbol, timeframe, bars + 10)` |
| 14 | Compute signals | `signals(candles, params, open_positions)` — pure, local |
| 15 | **Exits before entries** | `cancel_all()`, `place_order(sell, …, exit ref)`, `POST /api/trades/{id}/close` |
| 16 | Sizing base | `balances()` → EUR balance, else `stage_capital_eur` from the config |
| 17 | Per entry: size, feasibility, then the intent | `GET /api/trades?external_ref=…`, `POST /api/trades` |
| 18 | Entry order, carrying the plan's ref as `clientOrderId` | `place_order(buy, …, external_ref)` |
| 19 | Resting stop, **before** the journal is updated. Its own id: `external_ref[:29] + "sl"` | `place_stop(…, external_ref)` |
| 20 | Record the fill | `POST /api/trades/{id}/open` |
| 21 | Push state read fresh from the venue | `POST …/state` |
| 22 | Push events, finish the run | `POST …/events`, `PATCH …/runs/{run_id}` |

Steps 4, 5, 10 and 12 are the four early exits. Every one of them still
finishes the run — a run left `running` is a run the operator has to guess
about.

**After an order has reached the venue, an app call that fails is logged and
the run carries on.** It is never a reason to re-send. What the app misses,
the next run's reconciliation finds.

---

## 3. Endpoints

Base URL from `TRADE_LEDGER_URL`. Every call carries:

```
Authorization: Bearer <BOT_TOKEN>
```

The token is issued once when the bot is created in the UI (and once again on
rotate); the app only stores its sha256. A token is a key to **one** bot, and
`{slug}` in the path must be that bot's slug.

| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/api/bots/{slug}/heartbeat` | `HeartbeatIn` | `{"ok": true}` |
| `POST` | `/api/bots/{slug}/runs` | `RunStartIn` | `201 {"id": int}` |
| `PATCH` | `/api/bots/{slug}/runs/{run_id}` | `RunFinishIn` | `{"ok": true}` |
| `POST` | `/api/bots/{slug}/state` | `StateIn` | `StateOut` |
| `POST` | `/api/bots/{slug}/events` | `[EventIn, …]` | `201 {"inserted": int}` |
| `GET` | `/api/bots/{slug}/config` | — | `ConfigOut` |
| `POST` | `/api/bots/{slug}/commands/{command_id}/ack` | `AckIn` | `{"ok": true}` |

Errors, all of them final — a 4xx does not get better by asking again:

| Code | Meaning |
|---|---|
| 401 | no `Authorization` header on a push route, or a token that matches no bot |
| 403 | the token belongs to another bot (path slug ≠ token's bot; account or trade of another bot) |
| 404 | run, command or trade id that is not this bot's |
| 409 | journal lifecycle guard (e.g. opening a trade that is not `planned`) |
| 413 | more than 500 events in one call |
| 422 | payload the app rejects |
| 5xx | retry only `GET`/`PATCH` (see section 10) |

Money fields are **strings** on the wire (`"1234.56"`), never floats.
Timestamps are ISO 8601 with an offset (`"2026-09-09T12:00:00+00:00"`).

### 3.1 Heartbeat

```json
{
  "ts": "2026-09-09T12:00:00+00:00",
  "host": "vps-01",
  "code_version": "0.4.0",
  "next_run": "2026-09-09T16:05:00+00:00"
}
```

Every field is optional. `ts` defaults to now. `host` is accepted because
bots send it, but the registry owns that field — a bot cannot move itself
between `local` and `remote`. Response: `{"ok": true}`.

### 3.2 Start a run

```json
{"started": "2026-09-09T12:00:00+00:00"}
```

→ `201 {"id": 12}`. Keep the id; every later call about this run needs it.

### 3.3 Finish a run

`PATCH /api/bots/{slug}/runs/12`

```json
{
  "status": "ok",
  "summary": {"dry_run": false, "reconciliation": "ok", "entries": 1,
              "exits": 0, "orders": 2, "skipped": []},
  "error": null,
  "log": null
}
```

`status` ∈ `running`, `ok`, `error`, `dry_run`, `skipped`. `summary` is free
JSON. A non-null `log` is written to `data/bots/<slug>/runs/<run_id>.log` and
linked from the run. `status: "error"` also files an `error` event.
Response: `{"ok": true}`.

### 3.4 Push state

```json
{
  "ts": "2026-09-09T12:00:03+00:00",
  "equity_eur": "1000.00",
  "positions": [
    {"symbol": "BTC/EUR", "qty": "0.0031", "avg_entry": "58000.00",
     "stop_present": true, "stop_price": "55100.00",
     "unrealised_quote": "12.40"}
  ],
  "open_orders": [
    {"id": "12345", "clientOrderId": "okxdonchian49f2a1b3c4d5esl",
     "symbol": "BTC/EUR", "type": "stop", "side": "sell", "amount": "0.0031"}
  ],
  "reconciliation": "ok",
  "reconciliation_detail": null,
  "config_version": 7,
  "extra": {"balances": {"EUR": "820.10"}, "sizing_base_eur": "1000.00",
            "sizing_base_source": "EUR balance", "market_type": "spot"}
}
```

`reconciliation` ∈ `ok`, `mismatch`, `unknown`. `positions` and `open_orders`
pass straight through as JSON; the only field the app reads out of them is
`stop_present`, which kill rule K4 watches. `config_version` is the preset
`version_id` the run actually used. Response:

```json
{"ts": "2026-09-09T12:00:03+00:00", "equity_eur": "1000.00",
 "peak_equity_eur": "1120.00"}
```

One state row per bot, overwritten. `peak_equity_eur` only ever grows — the
drawdown kill rule measures against it. A push re-evaluates K1 and K4
immediately.

**Send `equity_eur` only when it is real equity in EUR.** The reference runner
sends `null` unless every configured pair settles in EUR and the venue
reported an EUR balance; on a USDT-quoted pair it has no FX rate (that lives
in the app) and would otherwise be pushing USDT as if it were EUR. The honest
`null` has a price: **kill rule K1 is inert without an equity number** — no
capital floor, no drawdown check. If you want K1 covering a bot, give it an
EUR-quoted account. `extra.sizing_base_eur` and `extra.sizing_base_source` say
what the run actually sized against either way.

### 3.5 Push events

The body is a **list**, at most 500 per call:

```json
[
  {"ts": "2026-09-09T12:00:01+00:00", "kind": "order",
   "message": "entry BTC/EUR", "payload": {"symbol": "BTC/EUR", "filled": "0.0031"}},
  {"kind": "info", "message": "state pushed: 1 position(s)", "payload": {}}
]
```

`kind` ∈ `heartbeat`, `info`, `warning`, `error`, `kill_rule`, `command`,
`config_applied`, `reconcile`, `order`. `ts` defaults to now.
Response: `201 {"inserted": 2}`.

### 3.6 Get config

`GET /api/bots/{slug}/config`

```json
{
  "bot": {
    "slug": "okx-donchian-4h",
    "account_id": 3,
    "dry_run": false,
    "paused_entries": false,
    "enabled": true,
    "mode": "paper",
    "venue": "okx",
    "stage_capital_eur": "1000.00"
  },
  "preset": {
    "version_id": 7,
    "version": 2,
    "strategy": "donchian",
    "params": {"entry": 55, "exit": 20, "atr_len": 20, "atr_mult": 2,
               "on_mismatch": "halt"},
    "timeframe": "4h",
    "pairs": ["BTC/EUR", "ETH/EUR"],
    "risk_pct": "0.5",
    "max_position_pct": "25",
    "leverage_cap": "1"
  },
  "commands": [
    {"id": 41, "kind": "pause", "reason": "drawdown",
     "issued_ts": "2026-09-09T11:58:00+00:00"}
  ]
}
```

`preset` is `null` when no preset version is assigned — the bot has no pairs
and must finish the run as `skipped`. `commands` are the ones not yet
acknowledged, oldest first.

`risk_pct` and `max_position_pct` may be `null`, and they do not mean the same
thing when they are. The reference runner reads `risk_pct or 0` — a null risk
sizes every entry to zero, which fails the feasibility check, so **no preset
risk means no entries**, not unlimited ones. A null (or zero)
`max_position_pct` means *no position cap*, because a preset that leaves the
field empty must still be tradable. `leverage_cap` is read as
`leverage_cap or 1`.

### 3.7 Acknowledge a command

`POST /api/bots/{slug}/commands/41/ack`

```json
{"result": "ok", "detail": "entries skipped this run"}
```

Only `result: "ok"` flips the corresponding bot flag app-side (`flat` and
`pause` → `paused_entries: true`; `resume` → `false`; `dry_run_on` /
`dry_run_off` → `dry_run`). Any other result leaves the command visibly
failed for the operator. Response: `{"ok": true}`.

---

## 4. Journal calls

Same bearer token; these are the ordinary trade endpoints, which recognise a
bot token and stamp `bot_id` on what it writes. A bot may only touch its own
account (403 otherwise) and its own trades (403 otherwise).

### 4.1 Plan (before the order exists)

`POST /api/trades` →

```json
{
  "account_id": 3,
  "instrument_id": 11,
  "direction": "long",
  "planned_entry": "58000.00",
  "planned_stop": "55100.00",
  "risk_eur": "8.99",
  "planned_qty": "0.0031",
  "note_pre": "close 58000 above 55-bar high 57200, ATR(20) 1450 [EUR balance, risk 8.99 over a 2900 stop]",
  "external_ref": "okxdonchian49f2a1b3c4d5e"
}
```

→ `201` with the full trade; `external_ref` on the response is the
`clientOrderId` the order must carry. `instrument_id` comes from
`GET /api/instruments` — the full list, matched on symbol **and** asset class
by the caller — or from `POST /api/instruments` with
`{"symbol", "asset_class", "quote_ccy", "price_source": "manual"}` when the
app does not know the instrument yet. `asset_class` follows the venue's market
type: `crypto` on spot, `perp` on swap.

### 4.2 `external_ref` and idempotency

The ref is derived, not random:

```
external_ref = non_alphanumerics_stripped(slug)[:12] + sha256(f"{slug}|{symbol}|{bar_ts.isoformat()}").hexdigest()[:12]
```

— 24 alphanumeric characters, inside OKX's 32-character `clientOrderId`
budget, the tightest of the venues. For the bot `okx-donchian-4h` that is
`okxdonchian4` plus twelve hex digits, e.g. `okxdonchian49f2a1b3c4d5e`. A
second run over the same bar computes the same ref.

Before planning, look it up:

`GET /api/trades?mode=all&external_ref=okxdonchian49f2a1b3c4d5e&page_size=2`
→ `{"items": [...], "total": 1}`

- no hit → plan it;
- hit with `status: "planned"` → reuse that trade, do not file a second one;
- hit with any other status (`open`, `closed`, `cancelled`) → this bar's entry
  is settled. **Do not order again.**

Two live orders may not share a client id, so the other two orders of a trade
derive their own from the entry's ref — the shared prefix still ties them to
the trade:

| Order | `clientOrderId` | Example |
|---|---|---|
| entry | `external_ref` | `okxdonchian49f2a1b3c4d5e` |
| resting stop | `external_ref[:29] + "sl"` | `okxdonchian49f2a1b3c4d5esl` |
| exit (market) | `external_ref[:31] + "x"` | `okxdonchian49f2a1b3c4d5ex` |

The `sl` suffix is also how the state push recognises a stop on a venue that
reports no trigger price (section 7).

### 4.3 Open (the entry filled)

`POST /api/trades/{id}/open`

```json
{"manual": {"ts": "2026-09-09T12:00:02+00:00", "quantity": "0.0031",
            "price": "58010.00", "fee_eur": "0"}}
```

→ the updated trade. `fee_eur` is `0` from a bot: the venue's fee is not
reliably on a market order, and the app's transaction import carries the real
number. The body also accepts `fill_ids` for the local UI; a bot uses
`manual`.

### 4.4 Close (the exit filled)

`POST /api/trades/{id}/close` — same body shape as open.

### 4.5 Cancel (a plan that never became a position)

`POST /api/trades/{id}/cancel`, no body → the cancelled trade. Used when the
venue refuses the stop and the entry is closed again. Cancelling releases the
ref from the "already filed" check in 4.2, so a re-run of the same bar does
not re-enter on it.

### 4.6 Open trades (the journal side of reconciliation)

`GET /api/trades?account_id=3&mode=all&status=open&page_size=500`

`mode=all` because the account decides the mode; the default `paper` would
hide a demo or live bot's own trades.

### 4.7 Backtest results (optional)

`POST /api/backtests` with the bot token stamps `bot_id` and, when `strategy`
is omitted, the bot's own strategy:

```json
{
  "strategy": "donchian", "preset_version_id": 7, "label": "55/20 4h 2019-2026",
  "period_start": "2019-01-01", "period_end": "2026-08-31",
  "data_source": "okx 4h ohlcv", "timeframe": "4h",
  "pairs": ["BTC/EUR"], "costs_note": "0,1 % taker, 0,05 % slippage",
  "trades": 84, "expectancy_r": "0.21", "profit_factor": "1.44",
  "win_rate": "38", "max_drawdown_pct": "22.5", "cagr_pct": "31.0",
  "benchmark_cagr_pct": "24.0", "benchmark_max_drawdown_pct": "38.0",
  "equity_r": [["2019-03-04", "0.9"], ["2019-05-11", "1.8"]],
  "notes": "parameters locked before the run"
}
```

→ `201` with the stored result, its `passed` verdict and `fail_reasons`.

---

## 5. Config, flags and commands

Everything a run needs comes from `GET config` — there is no local config
file beyond the env vars in section 8, and no private state file at all. The
journal plus the last pushed state *is* the bot's memory.

- `enabled: false` → finish the run `skipped`. Do not read the venue.
- `dry_run: true` (bot flag, or the `--dry-run` flag — either one is enough)
  → do everything except write to the venue: reconcile, apply commands,
  compute signals and sizes, **file the planned trade**, finish `dry_run`.
- `paused_entries: true` → skip entries; exits and stops still run.
- `stage_capital_eur` is the sizing fallback when the venue's balance is not
  in EUR, and the denominator kill rule K1 measures equity against.
- `preset.params` are strategy parameters plus `on_mismatch` (see section 7).

Commands arrive in the same payload and are acknowledged one by one, oldest
first, in the same run they were read:

| kind | What the bot does | Ack detail |
|---|---|---|
| `pause` | block entries for this run | `"entries skipped this run"` |
| `resume` | nothing local; the app clears `paused_entries` on the ack | — |
| `flat` | cancel every resting order, close every position, close the bot's open journal trades at the flatten's fill, push state, **end the run** | `"flattened N symbol(s)"` |
| `run_now` | nothing; being in this run *is* the response | — |
| `reload_config` | nothing; every run reloads anyway | — |
| `dry_run_on` | nothing local; the app sets `dry_run` on the ack | — |
| `dry_run_off` | nothing local; the app clears `dry_run` on the ack | — |
| anything else | ack with `result: "error"`, file a `command` event, carry on | `"unknown command: …"` |

`flat` blocks entries for this run too: the app sets `paused_entries` on the
ack, but that only reaches the bot with the *next* config. In a dry run a
`flat` is acknowledged with `"dry run: nothing was sent to the venue"` and
nothing is closed.

---

## 6. The strategy function

A strategy is one pure function plus its defaults, registered by name.

```python
def signals(
    candles: dict[str, list[Candle]],
    params: dict,
    open_positions: dict[str, dict],
) -> list[Signal]: ...

def default_params() -> dict: ...
```

- `candles` — `{symbol: [Candle, …]}`, oldest first, **completed bars only**.
  `Candle` (`trade_ledger/prices/service.py`) is a plain — *not* frozen —
  dataclass: `date`, `open`, `high`, `low`, `close`, the four prices
  `Decimal | None`. Skip bars with a `None` leg. The field is annotated
  `date` because the app's price history is daily, but the exchange wrapper
  puts an **aware `datetime`** in it, which is what a strategy on an intraday
  timeframe reads. Treat every candle as read-only even though nothing stops
  you writing to one.
- `params` — the preset's params. Merge over your own defaults:
  `p = default_params() | params`.
- `open_positions` — `{symbol: {"qty": Decimal}}` for the symbols the journal
  says are held. Membership is what matters; a symbol absent from the dict is
  flat.
- Returns `Signal(symbol, side, entry, stop, reason)`, a **frozen** dataclass
  (so two runs over the same bar compare equal):
  `side` is `"buy"` (entry) or `"sell"` (exit); `entry` and `stop` are
  `Decimal` for a buy and `None` for a sell (the runner sizes an exit from
  what is actually held); `reason` is the human sentence that ends up in the
  journal's `note_pre` and in the run summary.
- **Long only** today. A short entry would need a `side` on the plan and a
  sell entry in the runner; add it with the strategy that needs it.
- At most one signal per symbol per run.

**Determinism: the same candles, params and positions must produce the same
signals.** No clock, no network, no randomness, no file. That is what makes a
backtest comparable with the journal and a re-run of the same bar safe.

`Decimal` throughout — these numbers become an order size and a stop price.

Register it in `trade_ledger/botkit/strategies/__init__.py`:

```python
from . import donchian, my_strategy

STRATEGIES = {"donchian": donchian.signals, "my_strategy": my_strategy.signals}
DEFAULT_PARAMS = {"donchian": donchian.default_params(),
                  "my_strategy": my_strategy.default_params()}
```

The preset's `strategy` field is this key. An unknown key fails the run.

Copy `bots/template/strategy_template.py` to start; `trade-bot new <name>`
does the copy and writes the env file for you.

---

## 7. Kill rules, and what the bot honours locally

Five rules, K1–K5, defined in `trade_ledger/bots/kill_rules.py`. **The app
evaluates all five** — from the state, heartbeats, runs and closed trades the
bot pushed. The bot's job is to report honestly and to obey what comes back.

| Rule | Trips on | App's action |
|---|---|---|
| K1 | equity ≤ 80 % of stage capital, or ≥ 20 % below peak equity | queue `pause`, critical alert |
| K2 | profit factor < 1,0 over the last 20 closed trades | warning alert |
| K3 | 8 consecutive losing trades | warning alert |
| K4 | a position without a stop, a reconciliation mismatch, or 2 missed runs | critical alert; a stop-less position also queues `flat` |
| K5 | no heartbeat within `schedule_every_s + grace_s` | critical alert, status `stale` |

What the bot must do itself, because the app cannot reach the exchange:

1. **Report `stop_present` truthfully** on every pushed position, always as a
   boolean. K4 flags a position only when `stop_present is False`; a position
   dict that omits the key, or sends `null`, silently switches the rule off
   for that position — the one place where saying nothing is worse than
   saying "no". A stop is recognised by its trigger price
   (`stopLossPrice` / `triggerPrice` / `stopPrice` / `info.slTriggerPx`) or by
   the `sl` suffix on the client id, which is why the stop carries
   `external_ref[:29] + "sl"` (section 4.2).
2. **Never leave a position without a stop.** Place the stop immediately
   after the entry fill, before updating the journal. **If the venue refuses
   the stop: close the position again on the spot and cancel the plan.** Flat
   beats unprotected; the cancelled plan stops the next run of the same bar
   from entering all over again. The entry is not counted in the summary.
3. **Honour `paused_entries` and a `pause` command** — entries skipped, exits
   and stops still run.
4. **Reconciliation mismatch → stop trading.** Compare the app's open trades
   with what the venue holds, per symbol, with a 1 % relative tolerance.
   On a disagreement the policy is `preset.params["on_mismatch"]`:
   - `"halt"` (default) — push state with `reconciliation: "mismatch"` and
     the detail, push events, fail the run. Touch nothing at the venue.
   - `"flat"` — first cancel every resting order and close every position
     (cancel before close: on spot a resting sell locks the very coins the
     close would sell), then push and fail the run.
   Either way state goes up, because K4 reads `reconciliation` and a halt
   that pushes nothing leaves the operator with no alert at all.

   **A pending `flat` command is carried out first, under either policy.**
   The operator usually pressed the button *because* of the mismatch, and a
   halt would leave the command undeliverable and the position open. So:
   cancel and close every symbol, ack the command `ok`, close the bot's open
   journal trades at the flatten's fill, push state — and *still* finish the
   run as `error` with the mismatch detail. Flat is the safe state; the run
   is a failure regardless.
5. **After a commanded `flat`, end the run.** The positions the strategy
   would trade on no longer exist; an exit order against one of them would
   open a naked short.

---

## 8. Environment and running

`trade-bot --bot <slug> [--dry-run] [--once]`. Everything else comes from the
environment, so a token or an API key is never on a command line or in a
process listing.

| Variable | Meaning |
|---|---|
| `TRADE_LEDGER_URL` | where the app answers (default `http://HOST:PORT` from the app's `.env`, i.e. `http://127.0.0.1:8642`). A `local` bot never needs it: the supervisor sets it from the port the app was actually started with. Over the network this must be TLS — the token travels on every request |
| `BOT_TOKEN` | the bot's token, shown once when the bot was created or rotated. Missing → exit 1 without running |
| `EXCHANGE_ID` | ccxt id, e.g. `okx`. `fake` is a flat synthetic market for smoke tests and is refused without `--dry-run` |
| `EXCHANGE_KEY`, `EXCHANGE_SECRET`, `EXCHANGE_PASSPHRASE` | venue credentials: read + trade, **no withdrawal**, IP-restricted |
| `EXCHANGE_DEMO` | `1` for the venue's demo/sandbox (default), `0` for real money |
| `MARKET_TYPE` | `spot` or `swap`. Swap orders carry `tdMode=isolated`; see the leverage note below |

**Leverage.** On a swap the wrapper calls `set_leverage(min(cap, the market's
own maximum))` once per symbol, but `trade-bot` builds the exchange from the
environment alone and passes no cap, so the venue-side call is
`set_leverage(1)`. The preset's `leverage_cap` is enforced where it actually
binds — in **sizing**, as `qty ≤ leverage_cap × equity ÷ entry` — before the
lot-step rounding. A bot that raises the venue's leverage itself is breaking
this contract; the cap is a size limit, not a margin setting.

Exit codes: `0` when the run finished `ok`, `dry_run` or `skipped`; `1` for
anything else — an unreachable app, a reconciliation mismatch, a venue error.
The systemd unit and the supervisor read that number.

```bash
trade-bot new my_strategy                       # scaffold a strategy + env file
EXCHANGE_ID=fake uv run trade-bot --bot <slug> --dry-run   # no keys needed
uv run trade-bot --bot <slug> --dry-run         # real app, real venue reads, no orders
uv run trade-bot --bot <slug>                   # live
```

Scheduling: `host = local` lets the app's supervisor launch the bot on the
bot's own schedule (logs land in `data/bots/<slug>/runs/<id>.log`).
`host = remote` uses `bots/systemd/trade-bot@.{service,timer}` on a VPS.
**Keep the timer and `schedule_every_s` in step** — a timer that fires less
often than the registry says trips K5 every cycle.

---

## 9. Acceptance tests a bot must pass

The behaviours in `tests/test_runner.py`, in words. A new bot is not done
until it does all of these.

1. **A dry run plans the trade and sends nothing to the venue.** The run ends
   `dry_run`, a `planned` trade exists in the journal, the exchange saw zero
   writes.
2. **The demo path orders, stops, opens and pushes.** Entry order, then the
   stop, then `POST /api/trades/{id}/open`, then a state push whose position
   carries `stop_present: true`.
3. **An unreachable app never reaches the venue.** With the app down the run
   raises `AppUnreachable` and the exchange has zero writes.
4. **A reconciliation mismatch stops the run.** Under `on_mismatch: "flat"`
   everything is cancelled and closed first; under `"halt"` nothing at the
   venue is touched at all. Both push `reconciliation: "mismatch"` and fail
   the run.
5. **A pause command skips entries but still exits.** The command is acked
   `ok`, the exit order goes out, no entry does.
6. **A re-run over the same bar reuses the planned trade.** Same ref, same
   trade, no second plan and no second order.
7. **A disabled bot finishes `skipped`** without looking at the venue.
8. **A flat command squares the journal and ends the run.** Cancel, close,
   close the journal trades at the flatten's fill, push state, stop — no
   signals computed afterwards. In a dry run it touches neither venue nor
   journal.
9. **A refused stop closes the position instead of leaving it naked**, and
   cancels the plan behind it.
10. **A failure after an order still pushes what the venue now holds**, so a
    kill rule can see the truth.
11. **A journal failure after the order is logged, not retried.**
12. **The exit never sells more than the venue holds** (the smaller of journal
    and venue, floored to the lot step).
13. **The stop price is quantised to the tick before it is planned**, so the
    plan, the size and the resting order all measure R against the same stop.
14. **Sizing**: risk ÷ stop distance, capped by `max_position_pct` and
    `leverage_cap`, rounded **down** to the lot step; below the venue minimum
    the entry is skipped with a warning event, never rounded up; a stop equal
    to the entry is not a trade.

---

## 10. Do not

- **Do not retry a `POST`.** A 502 from a proxy does not say whether the app
  committed the write; a blind retry is how you get two planned trades — or
  two orders — for one signal. `GET` and `PATCH` are idempotent and may retry
  a connection failure or a 5xx three times, 1/2/4 s apart. Anything still
  unknown raises `AppUnreachable`.
- **Do not order without a plan.** No `POST /api/trades` first, no order.
- **Do not send the same order twice.** Derive the ref from the bar and check
  it (section 4.2).
- **Do not keep keys in the repo.** Env file only, `chmod 600`, `data/` and
  `/etc/trade-bot/` are outside version control.
- **Do not write to an exchange outside `botkit/exchange.py`.** One module,
  one audit surface.
- **Do not exceed the preset's `leverage_cap`** or `max_position_pct`. They
  are caps, not suggestions, and they are applied before the lot-step
  rounding.
- **Do not trade when `enabled` is false**, when `paused_entries` is true
  (entries), when there is no preset, or when reconciliation failed.
- **Do not leave a run `running`.** Always `PATCH` it to a final status.
- **Do not invent a fill.** If the venue reports no `filled`/`average`, fall
  back on the intended size and the bar's close, and say so in the event.
