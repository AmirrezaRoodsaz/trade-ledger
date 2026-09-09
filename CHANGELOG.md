# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-09-10

The Bot Center: the app now registers, configures, watches and controls trading
bots. It still never holds a trade-capable key and never places an order.

### Added

**Registry and push protocol**

- `Bot`, `Preset`/`PresetVersion`, `BotRun`, `BotState`, `BotEvent`,
  `BotCommand`, `BotDrill`, `Alert` and `BacktestResult` tables, plus
  `Trade.bot_id` so a bot's own trades are identifiable.
- Bearer-token auth for bots: `secrets.token_urlsafe(32)`, shown once on create
  and on rotate, stored as sha256. A token authorises its own bot and its own
  account — wrong token 401, foreign account 403. Requests without the header
  keep the previous local-UI behaviour.
- `POST /api/bots/{slug}/heartbeat`, `runs`, `runs/{id}`, `state`, `events`,
  `commands/{id}/ack` and `GET config`. Bots journal through the existing
  `/api/trades` routes, which stamp `bot_id` and pin the account.

**Health and control**

- Kill rules K1–K5 as pure functions — capital brake, rolling edge over the last
  20 closed trades, 8-loss streak, integrity (reconciliation, stops, missed
  runs) and heartbeat — with the status ladder `disabled`, `stale`, `error`,
  `paused`, `running`, `ok`.
- A monitor loop every 60 s: alerts with a six-hour per-(bot, kind) cooldown, a
  `pause` queued on K1 and a `flat` on a stop-less position.
- Command queue with acknowledgement: pause, resume (typed reason), emergency
  flat (typed confirmation), run now, dry-run on/off, reload config.
- Versioned, immutable presets; assigning one to a live bot needs a reason and
  queues `reload_config`.
- Telegram: alerts, a daily 07:00 summary, and inbound `/status`, `/pause`,
  `/resume`, `/flat`, `/runnow` from one chat id, behind
  `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

**Running bots**

- A supervisor loop that launches due `local` bots as subprocesses, captures
  their output to `data/bots/<slug>/runs/`, and caps failures at three per day.
- `bots/systemd/trade-bot@.service` and `.timer` for a `remote` bot on a VPS.
- `trade_ledger/botkit/`: `BotClient`, the `Exchange` wrapper (the only module
  in the repo with an exchange write call), the runner loop, a Donchian 55/20
  strategy, and the `trade-bot` CLI including `trade-bot new <name>`.
- `bots/BOT_CONTRACT.md`, `bots/README.md` and `bots/template/` — enough to hand
  to someone (or something) writing a new bot.

**Results and UI**

- Uploaded backtest results judged against configurable criteria, results per
  stage (backtest, incubation, real money) and a 0–100 % readiness score with a
  gate factor that caps a failing stage.
- Bots pages: fleet (`/bots`), detail with Overview, Strategy, Runs, Timeline,
  Config, Controls and Alerts (`/bots/:slug`), presets (`/bots/presets`), a Bots
  card on the dashboard and a Telegram section in Settings.
- `docs/bots.md`, a Bot Center section in `README.md` and in `docs/design.md`.

### Fixed

- `trade-bot` now reads the bot's own `data/bots/<slug>/.env`, as
  `bots/README.md` always said it did. Only the supervisor passed that file
  through before, so a bot started by hand or by systemd refused to run.

[0.2.0]: https://github.com/AmirrezaRoodsaz/trade-ledger/releases/tag/v0.2.0

## [0.1.0] — 2026-09-06

First release. Everything below is new.

### Ledger and sync

- SQLite schema for accounts, instruments, transactions, trades, playbooks,
  tags, daily notes, prices, FX rates, sync runs and settings. Money is
  `Decimal` end to end; timestamps are timezone-aware UTC.
- Unsigned amounts with the direction carried by the transaction type, applied
  by `cash_delta_eur` and `position_delta`.
- Idempotent upsert on `(account, external_id)`, with a content hash standing in
  where a venue supplies no id.
- Read-only adapters: Trading 212 over httpx, OKX and Kraken over `ccxt`. Sync
  runs are recorded with counts, warnings and errors; a failure never partially
  commits.
- CSV importers for Trading 212, OKX, Kraken, Binance and a generic format,
  behind a preview/commit API.
- Daily price and FX caching from Bitstamp, Stooq and the ECB, including
  back-filling transactions imported with `fx_source="pending"`.

### Journal

- Trade lifecycle plan → open → close → review (or cancel), with the plan
  required before the trade opens.
- Results in R, recomputed from linked fills or manual paper fills.
- Versioned playbooks, tags, mistakes, emotions, screenshots, daily notes.
- Obsidian-flavoured Markdown export and re-import.

### Analytics and portfolio

- Expectancy, win rate, profit factor, payoff, streaks, drawdown, adherence,
  equity curve, R histogram, calendar and breakdowns.
- MAE/MFE per trade from daily candles, with a chart against the plan.
- Stage gate: sample size, expectancy, adherence and drawdown checks.
- Holdings, allocation, value series, cash flows, dividends, fees, TTWROR and
  XIRR.

### Steuer

- Tax regime routing (§ 23, § 20 Aktien/INV/Termin/sonstige, § 22 Nr. 3).
- FIFO lots per tax wallet with holding periods, transfer carry-over, split
  handling and per-disposal gains.
- Year summary with the § 23 and § 22 Freigrenzen, the Sparerpauschbetrag, and
  per-lot Vorabpauschale.
- Anlage SO / KAP / KAP-INV line mapping for VZ 2025, plus disposal, lot,
  Blockpit and CoinTracking CSV exports and a DAC8 venue reconciliation view.
- Every tax response carries the disclaimer and the form status.

### Reports and UI

- One-page weekly PDF: stats, equity curve, calendar, open trades, open lots,
  tax meters.
- React frontend: Dashboard, Account, Transactions, Journal, Analytics,
  Portfolio, Steuer, Reports and Settings, served by the backend at `/`.

### Tooling

- `trade-ledger` CLI: `serve`, `sync`, `import`, `export-notes`,
  `import-notes`, `prices`, `tax-year`, `report`.
- CI on every push: ruff, pytest, gitleaks, then the frontend check and build.
- gitleaks and ruff as pre-commit hooks.

[0.1.0]: https://github.com/AmirrezaRoodsaz/trade-ledger/releases/tag/v0.1.0
