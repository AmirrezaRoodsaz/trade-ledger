# Bots

Two documents do the actual work; this page says which one you want.

| You want to… | Read |
|---|---|
| **write a bot** — the loop in order, every endpoint with its JSON, the strategy signature, the env variables, the acceptance tests | [`bots/BOT_CONTRACT.md`](../bots/BOT_CONTRACT.md) |
| **run one** — create it in the UI, write its env file, dry-run it, schedule it locally or on a VPS | [`bots/README.md`](../bots/README.md) |
| **understand the app side** — tables, push protocol, health model, presets, supervisor, readiness | [`design.md`](design.md#bot-center) |

A copyable strategy module is `bots/template/strategy_template.py`, and
`uv run trade-bot new <name>` writes it plus an env file for you.

## The four rules a bot lives under

1. **The app never holds trade-capable keys and never places an order.** The
   only module with an exchange write call is `trade_ledger/botkit/exchange.py`,
   which no part of the app process imports — `tests/test_exchange_isolation.py`
   fails the build if that changes. Bot keys live in the bot's own env file.
2. **Journal first.** Every entry is filed as a planned trade through
   `/api/trades` before the order exists, and the trade's `external_ref` is the
   exchange `clientOrderId`. A bot that cannot reach the app does not trade.
3. **Kill rules are evaluated by the app, from its own data.** A dead bot
   cannot silence its own alarm.
4. **Control is soft and acknowledged.** The app queues a command; the bot pulls
   it at its next run and acks it with a result. Until then the UI says
   "queued", not "done".

## Kill rules: who evaluates what

The app computes all five every 60 seconds and on every state push. The bot
enforces its own local half during a run — it is the only side that can talk to
a venue.

| Rule | What it measures | Data it reads | Trigger | App does | Bot does |
|---|---|---|---|---|---|
| **K1** capital brake | equity against the stage capital and against its own peak | the bot's last `BotState` | equity ≤ 80 % of stage capital, **or** ≥ 20 % below peak equity | critical alert, queues `pause`. A bot that holds positions but reports no equity in EUR gets a **warning** instead: K1 cannot evaluate at all, and a quote currency that is not EUR is why — use an EUR-quoted account | applies the `pause` at its next run: no entries, exits and stops still run |
| **K2** rolling edge | profit factor over the last 20 closed trades of *this* bot | the journal | ≥ 20 closed trades and profit factor < 1,0 | warning alert | nothing — this one is a judgement call for the operator |
| **K3** streak | consecutive losing trades | the journal | 8 losses in a row | warning alert | nothing |
| **K4** integrity | reconciliation, stops, missed runs | `BotState.reconciliation`, `positions[].stop_present`, the last `BotRun` | mismatch, a position without a stop, or 2 missed schedule slots | critical alert; queues `flat` **only** for a stop-less position on a bot that is still heartbeating | reconciles at the start of every run and refuses to trade on a mismatch (`on_mismatch`: `halt` by default, `flat` to close out first); carries out a pending `flat` command even during a mismatch — cancel, close, ack, square the journal, then still fail the run; closes a position again on the spot if the venue refuses its stop |
| **K5** heartbeat | silence | `last_heartbeat` vs `schedule_every_s + grace_s` | deadline passed | critical alert, status `stale` | cannot — that is the point |

Alerts dedupe per (bot, kind) for six hours, and so do the commands the monitor
queues. Acknowledging an alert does not reopen the window.

`flat` and `pause` reach the venue only through the bot. The app closing a
position itself would need a trade-capable key, which is exactly what rule 1
forbids.
