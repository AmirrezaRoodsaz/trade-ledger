# trade-ledger

A local, single-process trade and investment logger for a German private
investor. It pulls read-only history from Trading 212, OKX and Kraken (or eats
their CSV exports), keeps a disciplined trading journal in **R** rather than
euros, computes portfolio value and returns from the ledger itself, and turns
the whole year into the numbers the German forms ask for — Anlage SO, KAP and
KAP-INV — with FIFO lots, the § 23 twelve-month clock and the Vorabpauschale
worked out per lot. Nothing leaves the machine: one SQLite file, one FastAPI
process, one React frontend it serves itself.

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

**Reports**
- One-page weekly PDF: headline stats, equity curve, monthly calendar, open
  trades, open crypto lots and the two tax meters.

## Architecture

One process, three layers, no services to orchestrate.

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
        └── ledger.py            sign conventions, upsert, aggregates
        ▼
SQLite (data/ledger.db)          SQLAlchemy 2.0, Decimal money via a type decorator
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

## Security model

- **Read-only keys only.** No function in this repo places, cancels, modifies
  or withdraws anything. The adapters call history and balance endpoints and
  nothing else. Create keys with read/query permission only —
  `docs/setup-venues.md` walks through each venue.
- **`.env` is never committed.** It is gitignored, and so are `data/`,
  `exports/`, `screenshots/`, `notes_out/`, `*.db`, `frontend/dist/` and
  `node_modules/`. Secrets are read from the environment first and from `.env`
  second; the settings API reports only *whether* a key is set, never its value.
- **gitleaks** runs as a pre-commit hook and again in CI on every push.
- **Local only.** The server binds `127.0.0.1` and there is no auth layer,
  because there is nothing to authenticate to — do not expose the port.
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
- No Binance or IBKR API adapter (CSV import only), no alerting, no mobile app.

## Development

```bash
uv run pytest -q          # 289 backend tests
uv run ruff check .
cd frontend && npm run check && npm run build
pre-commit install        # ruff + gitleaks on every commit
```

## License

MIT — see [LICENSE](LICENSE).
