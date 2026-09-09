# trade-ledger

A local, single-process trade and investment logger for a German private
investor. It pulls read-only history from Trading 212, OKX and Kraken (or eats
their CSV exports), keeps a disciplined trading journal in **R** rather than
euros, computes portfolio value and returns from the ledger itself, and turns
the whole year into the numbers the German forms ask for — Anlage SO, KAP and
KAP-INV — with FIFO lots, the § 23 twelve-month clock and the Vorabpauschale
worked out per lot. Nothing leaves the machine: one SQLite file, one FastAPI
process, one React frontend it serves itself.

It also runs the trading bots that feed that journal — as separate processes it
registers, configures, watches and can stop, but whose exchange keys it never
holds and whose orders it never places. See [Bot Center](#bot-center).

<!-- screenshot: Dashboard -->
<!-- screenshot: Journal, a closed trade with its chart and R -->
<!-- screenshot: Steuer, the year summary and the Anlage line mapping -->

## Features

**Ledger and sync**
- Accounts per venue with a `mode` of `live`, `paper` or `demo`; no endpoint
  ever mixes modes unless asked for `mode=all`.
- Read-only API sync: Trading 212 (orders, dividends, cash transactions,
  positions), OKX and Kraken through `ccxt` (trades, deposits, withdrawals,
  staking rewards, balances).
- CSV importers for Trading 212, OKX, Kraken, Binance and a generic format,
  with a preview step before anything is written.
- Idempotent upserts on `(account, external_id)`, so re-syncing an overlapping
  window adds nothing twice.
- Daily prices and FX cached locally: Bitstamp (crypto), Stooq (equities and
  funds), ECB reference rates. Transactions that arrive without a EUR value are
  marked `fx_source="pending"` and filled in once the rate is available.

**Journal**
- Plan → open → close → review, with the plan (entry, stop, target, risk in
  euro) written *before* the trade, not after.
- Result is reported in R — realised P&L divided by the risk planned up front.
- Fills come from real ledger rows or from manual entries for paper trading;
  average entry/exit, fees and result are recomputed from them.
- Versioned playbooks, tags, mistakes, pre/post emotion, screenshots, and daily
  notes.
- Export and re-import trades as Obsidian-flavoured Markdown notes.

**Analytics**
- Expectancy, win rate, profit factor, payoff, streaks, max drawdown, plan
  adherence, equity curve, R histogram and a trade calendar.
- Breakdowns by playbook, symbol, weekday, session or tag.
- MAE/MFE per trade from daily candles, plotted against the plan.
- A stage gate: minimum sample size, positive expectancy, ≥ 90 % adherence and
  drawdown inside 20 % of capital, so "am I allowed to size up" is a
  calculation and not a mood.

**Portfolio**
- Holdings, allocation by asset class, venue or instrument, value series,
  cash flows, dividends (with a naive next-12-month projection) and fees.
- TTWROR and XIRR over any date range.
- Days remaining until each crypto holding leaves the § 23 holding period.

**Steuer**
- Tax regime routing per transaction: § 23 EStG (crypto spot, FX cash),
  § 20 (shares, funds via KAP-INV, Termingeschäfte, dividends and interest),
  § 22 Nr. 3 (staking rewards, airdrops received for a service).
- FIFO lots per tax wallet, holding periods, per-disposal gains, transfers that
  carry their acquisition date across wallets.
- Year summary with the § 23 Freigrenze (1.000 €), the § 22 Freigrenze (256 €)
  and the Sparerpauschbetrag, plus Vorabpauschale per fund lot.
- A line-by-line mapping onto Anlage SO / KAP / KAP-INV for VZ 2025.
- CSV exports: the ledger's own disposals and lots, plus Blockpit and
  CoinTracking import templates.
- A DAC8 reconciliation view: what each venue would report for the year, next
  to what the ledger holds.

**Bots**
- A registry of bot processes with bearer-token auth, health status, run
  history, an event timeline and an alert list.
- Kill rules K1–K5 evaluated by the app, not by the bot: capital brake, rolling
  edge, loss streak, integrity and heartbeat.
- Versioned presets, a soft acknowledged command queue (pause, resume, flat,
  run now, dry-run, reload config), a local supervisor and systemd units for a
  VPS, and Telegram alerts with inbound commands.
- Readiness 0–100 % per bot and a strategy dashboard: backtest, incubation and
  real money in three columns with the same metrics.
- `botkit`: the client, the exchange wrapper, the runner loop, a Donchian
  strategy and the `trade-bot` CLI, so a bot is a strategy file and an env file.

**Reports**
- One-page weekly PDF: headline stats, equity curve, monthly calendar, open
  trades, open crypto lots and the two tax meters.

## Bot Center

Trading bots are separate Python processes. The app is their registry, health
monitor, configuration source, command queue and alert channel — and, through
the journal, their trade record. It is not their runtime, and it never touches
an exchange.

Four rules the whole area is built around:

- **The app never holds trade-capable keys and never places an order.** The
  only module in the repo with an exchange write call is
  `trade_ledger/botkit/exchange.py`, which no part of the app process imports;
  a test fails the build if that ever changes. A bot's keys live in its own env
  file under `data/bots/<slug>/.env` (gitignored, `chmod 600`), or in the VPS
  environment.
- **Journal first.** Every entry is filed as a planned trade through
  `/api/trades` before the order exists, and the trade's `external_ref` is the
  exchange `clientOrderId`. A bot that cannot reach the app does not trade.
- **Kill rules are evaluated independently of the bot**, in the app, from the
  app's own data. A dead bot cannot silence its own alarm.
- **Control is soft and acknowledged.** The app queues a command; the bot pulls
  it at its next run and acks it with a result. The UI says "queued" until then.

### Registering and running one

**Bots → New bot**: name, account, strategy, host, schedule. The bearer token is
shown **once**, on creation and on rotate — the app keeps only its sha256. Then

```bash
mkdir -p data/bots/<slug> && cp bots/template/bot.env.example data/bots/<slug>/.env
chmod 600 data/bots/<slug>/.env      # BOT_TOKEN, TRADE_LEDGER_URL, exchange keys
uv run trade-bot --bot <slug> --dry-run
```

A dry run does everything except order. With no keys at all,
`EXCHANGE_ID=fake` in that file smoke-tests the loop against a flat synthetic
market; `fake` is refused without `--dry-run`.

A bot with `host = local` is then launched on schedule by the app's own
supervisor — start `trade-ledger serve` and leave it running. A `remote` bot on
a VPS is scheduled by the shipped systemd units instead
(`bots/systemd/trade-bot@.service` and `.timer`), reaching the app over the
network with `TRADE_LEDGER_URL`; put TLS in front of it, the token is on every
request. `bots/README.md` has both walkthroughs.

### What the app does with it

- **Kill rules K1–K5** — capital brake, rolling edge, loss streak, integrity
  (reconciliation, stops, missed runs) and heartbeat — evaluated every 60 s and
  on every state push. K1 queues a `pause`, K4 a `flat` for a stop-less
  position; the rest alert. Alerts dedupe per (bot, kind) for six hours.
  `docs/bots.md` has the table of who evaluates what.
- **Telegram**, when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set:
  alerts, a daily 07:00 summary, and inbound `/status`, `/pause <slug>`,
  `/resume <slug> <reason>`, `/flat <slug> CONFIRM`, `/runnow <slug>` — from
  that chat id only. Settings has a test-message button.
- **Presets**: versioned parameter sets (params, timeframe, pairs, `risk_pct`,
  `max_position_pct`, `leverage_cap`). A version is immutable; a change is a new
  version. Assigning one to a live bot needs a typed reason and queues
  `reload_config`, and the UI shows "pending" until the bot acks.
- **Readiness 0–100 %** over four weighted stages — backtest passed 25 %, five
  manual drills 15 %, incubation 30 %, live 30 % — each capped by a gate factor
  so a failing stage gate cannot look finished. The strategy dashboard puts
  backtest, incubation and real money in three columns with the same metrics and
  the same equity curve in R. The app does not backtest; a result is uploaded as
  JSON and judged against configurable criteria.

### Writing a new bot

Hand [`bots/BOT_CONTRACT.md`](bots/BOT_CONTRACT.md) to whoever (or whatever)
writes it — it is self-contained: the loop in order, every endpoint with its
JSON, the strategy signature, the env variables, the kill rules a bot honours
locally, and the acceptance tests it has to pass. `uv run trade-bot new <name>`
scaffolds a strategy module and an env file to fill in.

## Architecture

One process, three layers, no services to orchestrate. Bots are the one thing
outside it, and they are subprocesses talking HTTP, not a service mesh.

```
React + Vite (frontend/)         built to frontend/dist
        │  fetch /api/...
        ▼
FastAPI (trade_ledger/api/)      routers auto-discovered, all under /api
        │                        also serves dist at / and screenshots at /screenshots
        ├── engine/              pure computation: analytics, MAE/MFE, portfolio, returns
        ├── tax/                 regime → FIFO → year summary → forms → exports
        ├── adapters/            read-only venue APIs (httpx, ccxt)
        ├── importers/           venue CSV → TxDraft
        ├── prices/              Bitstamp / Stooq / ECB, cached
        ├── bots/                registry, kill rules, presets, commands, alerts
        └── ledger.py            sign conventions, upsert, aggregates
        │
        ├─ background loops (only under `trade-ledger serve`)
        │    monitor      every 60 s: kill rules → alerts, system commands, status
        │    supervisor   launches due local bots as subprocesses, reaps them
        │    telegram     inbound commands and the daily summary, when configured
        ▼
SQLite (data/ledger.db)          SQLAlchemy 2.0, Decimal money via a type decorator

trade_ledger/botkit/             a separate process, started by the supervisor
        │                        or by systemd; talks to the app over HTTP
        └── exchange.py          the one module allowed to write to a venue
```

Money is `Decimal` end to end in the backend and formatted `de-DE` in the UI
(`1.234,56 €`, `06.09.2026`). `docs/design.md` has the tables, the sign
conventions and how the tax engine routes a transaction.

## Quick start

Requires Python 3.13+ with [uv](https://docs.astral.sh/uv/) and Node 22+.

```bash
git clone https://github.com/AmirrezaRoodsaz/trade-ledger.git
cd trade-ledger

uv sync                                  # backend deps
cd frontend && npm ci && npm run build   # builds frontend/dist
cd ..

cp .env.example .env                     # optional: fill in read-only API keys
uv run trade-ledger serve                # http://127.0.0.1:8642, opens a browser
```

The database, `data/` and everything under it are created on first run. Without
a `.env` the app still works fully — you just import CSVs and enter trades by
hand instead of syncing.

For frontend development, `cd frontend && npm run dev` serves on :5173 and
proxies `/api` to the backend on :8642.

## CLI

```
uv run trade-ledger serve [--port N] [--no-browser]
uv run trade-ledger sync (--account NAME | --all)
uv run trade-ledger import --account NAME --format trading212|okx|kraken|binance|generic FILE
uv run trade-ledger export-notes [--dir DIR]
uv run trade-ledger import-notes --dir DIR --account NAME
uv run trade-ledger prices --refresh
uv run trade-ledger tax-year YEAR [--mode live|paper|demo|all]
uv run trade-ledger report [--week YYYY-MM-DD] [--mode ...]
```

Bots run under their own command, in their own process:

```
uv run trade-bot --bot SLUG [--dry-run] [--once]
uv run trade-bot new NAME          # scaffold a strategy module and an env file
```

`trade-bot` reads `data/bots/<slug>/.env` (and the environment, which wins) for
the token and the exchange keys, so neither is ever on a command line.

## Security model

- **Read-only keys only in the app.** No function in the app process places,
  cancels, modifies or withdraws anything. The adapters call history and balance
  endpoints and nothing else. Create keys with read/query permission only —
  `docs/setup-venues.md` walks through each venue.
- **One write boundary, enforced by a test.** The single module in the repo with
  an exchange write call is `trade_ledger/botkit/exchange.py`, imported only by
  a bot process. `tests/test_exchange_isolation.py` imports `trade_ledger.main`
  and asserts that module is not in `sys.modules`, so the boundary fails the
  build rather than drifting.
- **Bot keys never reach the app.** Each bot has its own env file under
  `data/bots/<slug>/.env` (`chmod 600`, `data/` is gitignored) or
  `/etc/trade-bot/<slug>.env` on a VPS. The supervisor passes the file to the
  child process without reading it; the API reports only *whether* each key is
  set.
- **`.env` is never committed.** It is gitignored, and so are `data/`,
  `exports/`, `screenshots/`, `notes_out/`, `*.db`, `frontend/dist/` and
  `node_modules/`. Secrets are read from the environment first and from `.env`
  second; the settings API reports only *whether* a key is set, never its value.
- **Bot tokens** are `secrets.token_urlsafe(32)`, shown once on creation and on
  rotate; only their sha256 is stored, so a copied database cannot talk to the
  app as a bot. A token authorises its own bot and its own account only — the
  wrong token is a 401, another bot's account a 403. Requests without the header
  are the local UI and behave as before.
- **gitleaks** runs as a pre-commit hook and again in CI on every push.
- **Local only.** The server binds `127.0.0.1` and there is no user auth layer,
  because there is nothing to authenticate to — do not expose the port. A bot on
  another machine is the one exception, and it needs TLS in front of the app:
  its token travels on every request.
- Test fixtures are synthetic. No real account ids or API responses are stored
  in the repo.

## Disclaimer

> **Berechnung — mit Steuerberater prüfen.** The tax module computes numbers; it
> does not give tax advice and it does not file anything. Every legal fact it
> relies on is listed in `docs/tax-notes.md` with the date it was checked and a
> VERIFY flag where it came from a secondary source. Check the output against
> the official forms and your Steuerberater before it goes anywhere near a
> Steuererklärung.

Nothing here is investment advice either.

## Status and limitations

Working and tested end to end, with these known edges:

- **OKX** returns roughly three months of history over the API. Older data has
  to come from the quarterly archive CSV; the sync warns when it hits the wall.
- **Trading 212** sync covers Invest and ISA accounts. CFD history is CSV only.
  Dividend withholding tax is not in the current API response, so it stays 0
  until entered by hand.
- **MAE/MFE** is computed from daily candles, so an intraday spike that reverses
  before the close is invisible. Fine for swing trades, not for scalps.
- **Form line numbers** are VZ 2025 and were taken from secondary sources
  describing the forms, not from the official PDFs. Same for the Basiszins
  values behind the Vorabpauschale. All of them are flagged in the output.
- **Portfolio cost basis** is a running average for display; the tax engine runs
  its own FIFO and the two are deliberately not the same number.
- **Supervisor state is in memory.** Which local bot it launched, when, and
  where the log went is lost on an app restart; the bot process itself keeps
  running. The run row it opened is then closed by whatever the bot reports, or
  caught by kill rule K4 as a missed run.
- **Bot sizing assumes EUR-quoted markets.** Risk is sized off the venue's EUR
  balance when every pair settles in EUR. On a USDT-quoted pair the bot has no
  FX rate — that lives in the app — and falls back on its `stage_capital_eur`,
  treating 1 USDT as 1 EUR. Sizes are off by the EUR/USD rate on those pairs;
  the state push says which base was used.
- **Spot reconciliation counts every coin in the account**, so a coin bought by
  hand in the same account looks to the bot like a position it opened and shows
  up as a mismatch. Give each bot its own venue sub-account.
- **The Telegram daily summary is guarded in memory only.** An app restart at
  07:00 local can send it twice.
- No Binance or IBKR API adapter (CSV import only), no mobile app.

## Development

```bash
uv run pytest -q          # 624 backend tests
uv run ruff check .
cd frontend && npm run check && npm run build
pre-commit install        # ruff + gitleaks on every commit
```

## License

MIT — see [LICENSE](LICENSE).
