# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

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
