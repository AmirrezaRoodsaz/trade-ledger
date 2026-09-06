# Design

How trade-ledger is put together, written from the code. Read this before
changing the ledger, the journal or the tax engine — most of the subtlety is in
the conventions, not the code.

## Shape

One process. FastAPI serves the JSON API under `/api`, the built frontend at
`/` (with an SPA fallback, so a hard reload of `/journal` still works) and
uploaded screenshots at `/screenshots`. SQLAlchemy 2.0 talks to one SQLite
file. There is no queue, no cache server and no auth layer — the server binds
`127.0.0.1` and the data never leaves the machine.

```
trade_ledger/
  settings.py      Settings from .env; env_status() reports presence, never values
  db.py            engine, SessionLocal, init_db, Money / UTCDateTime type decorators
  models.py        every table
  enums.py         the string enums that are also the wire format
  ledger.py        sign conventions, instrument dedup, idempotent upsert
  importers/       venue CSV -> TxDraft, registry in __init__
  adapters/        read-only venue APIs; base.py dispatches and records SyncRuns
  prices/          ecb, bitstamp, stooq + service.py orchestrating the cache
  journal.py       trade lifecycle and the numbers derived from fills
  notes.py         Markdown note export/import
  engine/          pure computation: analytics, mae_mfe, portfolio, returns
  tax/             regime -> fifo -> year_summary -> anlage/forms -> exports
  reports/weekly.py  the one-page PDF
  api/             routers, auto-discovered; every module with a `router` is mounted
  main.py, cli.py  app factory and command line
```

`api/__init__.py` walks the package and collects every module-level `router`,
so adding an endpoint file is enough — there is no registry to update. The
`/api` prefix is applied once, in `create_app`, so routers declare plain paths.

## Money and time

- Money is `Decimal` everywhere in the backend, stored through a `Money` type
  decorator. Never float. The frontend receives money as **strings** and formats
  them `de-DE` (`1.234,56 €`).
- Timestamps are timezone-aware UTC through a `UTCDateTime` decorator; SQLite
  has no native tz, so the decorator re-attaches UTC on the way out.
- Enum columns store the enum's string value as plain text. `StrEnum` members
  compare equal to their raw string, so callers may pass either.

## Tables

| Table | Holds |
|---|---|
| `accounts` | one per venue account: `venue`, `kind`, `mode`, `base_ccy`, `credential_env_prefix`, `tax_wallet`, `active` |
| `instruments` | unique on `(symbol, asset_class)`: `isin`, `quote_ccy`, `price_source`, `price_symbol`, `fund_type`, `tax_regime` override |
| `transactions` | the ledger. Unique on `(account_id, external_id)` |
| `trades` | the journal: plan, execution, review, `screenshots`, `tags` |
| `playbooks`, `playbook_versions` | versioned rule sets a trade can point at |
| `tags`, `daily_notes` | journal metadata |
| `prices`, `fx_rates` | the daily cache, unique on `(instrument, date)` and `(ccy, date)` |
| `sync_runs` | one row per sync attempt: `status`, `added`, `skipped`, `error` |
| `settings` | key/value overrides (for example `basiszins_2025`) |

### Account kinds and modes

`kind ∈ {broker_invest, broker_cfd, crypto_spot, crypto_derivatives, wallet}`
decides the tax treatment of what sits in the account.
`mode ∈ {live, paper, demo}` decides whether it is real money. Every stats
endpoint takes a `mode` filter and never mixes modes unless `mode=all` is asked
for explicitly. Tax routes default to `live`, because tax is about real money;
the other routes default to `paper`.

`tax_wallet` defaults to the account name and is the FIFO pool key. Two
accounts that are the same wallet for tax purposes can share one.

## Sign conventions

The single most important rule: **stored amounts are unsigned.** `quantity`,
`fee_eur` and `amount_eur` are non-negative — the only exception is
`adjustment.amount_eur`, which may be signed. Direction lives in the
transaction *type*, not in a minus sign, and is applied by two pure functions
in `ledger.py`. The same validator guards the CSV path and the manual-entry
API, so both write paths enforce it identically.

`cash_delta_eur(tx)` — effect on the EUR cash balance:

| Type | Cash delta |
|---|---|
| `buy` | −(amount + fee) |
| `sell` | +amount − fee |
| `deposit` | +amount − fee |
| `withdrawal` | −(amount + fee) |
| `dividend`, `interest`, cash-only `staking_reward` | +amount − fee − withholding |
| `fee` | −amount |
| `adjustment` | +amount (signed) |
| `transfer_in`, `transfer_out`, in-kind rewards, `split`, `vorabpauschale` | 0 |

`position_delta(tx)` — effect on the instrument position:

| Type | Position delta |
|---|---|
| `buy`, `transfer_in`, `staking_reward`, `airdrop`, `split` | +quantity |
| `sell`, `transfer_out` | −quantity |
| everything else, or no instrument | 0 |

A crypto deposit is a `transfer_in`, not a `deposit`: `deposit` and `withdrawal`
mean fiat cash. This matters for tax — a transfer moves lots between wallets
without restarting the holding period, a deposit does not touch lots at all.

## Ingestion

Both routes converge on `TxDraft`, a transaction that has not yet been resolved
against the DB: it names its instrument by `(symbol, asset_class)` rather than
by id. `upsert_transactions` resolves or creates the instrument, then inserts
only drafts whose `(account, external_id)` is new. Drafts without a venue id get
a deterministic hash of their content as `external_id`, so re-importing the same
export is a no-op. The insert and the commit are one unit — a failure leaves
zero new rows, never half a sync.

**Adapters** (`adapters/base.py`) create a `SyncRun`, ask for everything since
the last successful run *minus a seven-day overlap* (late-settling items), upsert
and record the outcome. Credentials come from `{prefix}_API_KEY` and friends; a
missing credential ends the run as `error` rather than raising through the API.
Adapters expose `fetch_transactions` and `fetch_balances` and nothing else.

**Importers** (`importers/<venue>.py`) each expose `parse(bytes) -> ImportResult`
with drafts and per-row errors. Multi-row events are collapsed first: Kraken
groups by `refid`, Binance by `UTC_Time`, so a trade's two legs become one
BUY or SELL.

**Prices** are fetched daily and cached: Bitstamp for crypto (already EUR),
Stooq for equities and funds (native quote currency), ECB for FX. A transaction
whose EUR value could not be determined at import time is stored with
`fx_source="pending"`; `prices/service.py` fills it in on the next refresh. Tax
and portfolio code both warn rather than silently treating a pending amount as
zero.

## Journal

A `Trade` moves `planned → open → closed`, or `planned → cancelled`. The plan —
entry, stop, target and **risk in euro** — is written before the trade opens,
which is what makes R meaningful afterwards.

A "fill" is a `Transaction` of type BUY or SELL whose `trade_id` points at the
trade. Fills come either from a synced row the user links by id, or from a
manual entry for paper trading where no venue row exists. `avg_entry`,
`avg_exit`, `quantity`, `fees_eur` and `result_eur` are always recomputed from
the fills, never typed in.

- **R** = `result_eur / risk_eur`. Undefined without a planned risk, and the API
  says `null` rather than guessing.
- **MAE/MFE** come from daily candles over the trade's life, so an intraday
  spike that reverses before the close is invisible. `mae_r`/`mfe_r` divide by
  the same planned risk.
- **Adherence** is a boolean the review step sets, plus an optional `mistake`
  from a fixed vocabulary. It feeds the stage gate, which is the point of
  recording it.

## Engines

`engine/` is pure: every function takes ORM rows or duck-typed objects and
returns dataclasses. No sessions, so everything is testable against a hand
calculation.

- `analytics.py` — expectancy (mean R over closed trades), win rate, profit
  factor, payoff, streaks, max drawdown, adherence, equity curve, R histogram,
  calendar, breakdowns, and the **stage gate**: minimum closed trades (30 paper,
  50 live), expectancy above zero, adherence ≥ 90 %, max drawdown within 20 % of
  capital. `mode=all` has no gate — a mixed sample is not a stage.
- `portfolio.py` — holdings, cash flows, valuation, allocation, dividends, fees.
  Cost basis here is a **running average**, a display figure only; the tax engine
  runs its own FIFO and the two numbers are deliberately different.
- `returns.py` — TTWROR and XIRR over `(date, Decimal)` pairs.
- `mae_mfe.py` — daily-resolution excursions.

## Tax engine

Four steps, each its own module, each pure except where it needs prices.

### 1. Regime (`tax/regime.py`)

`regime_for(tx, instrument, account)` answers which German rule applies:

| Input | Regime |
|---|---|
| `instrument.tax_regime` set | that override, always wins |
| `dividend`, `interest` | `p20_sonstige` |
| `staking_reward` | `p22` |
| `airdrop` with a value | `p22`; with no value on receipt, `none` |
| crypto in a `crypto_spot` or `wallet` account | `p23` |
| crypto in a `crypto_derivatives` or `broker_cfd` account | `p20_termin` |
| stock | `p20_aktien` · ETF | `p20_inv` · FX | `p23` · perp/CFD | `p20_termin` |

Anything unclassified is `none` and produces a warning rather than quietly
landing in a bucket.

### 2. FIFO (`tax/fifo.py`)

`run_fifo` replays every transaction as lots, one pool per
`(tax_wallet, instrument)`. It is a pure function over lists — it never touches
the session.

- Transactions are sorted by `(ts, rank, id)` where a `transfer_out` ranks
  before the `transfer_in` it feeds, so a same-timestamp move between wallets
  cannot consume lots that have not arrived yet.
- **Acquisition fees raise the cost basis** (Anschaffungsnebenkosten);
  **disposal fees are deducted from proceeds** (Werbungskosten).
- A disposal is tax-free once the asset was held for **more than** one year, so
  the comparison is strictly `sale_date > acquired + 1 year`.
- A transfer between own wallets carries acquisition date and cost across —
  the holding period is not restarted. Transfers are matched by `link_id`; an
  unlinked one warns and drops or invents the cost basis rather than pretending.
- A disposal larger than the pool assumes a zero-cost lot and warns (or raises
  `InsufficientLots` in strict mode).
- Splits distribute the new quantity over the open lots by size, leaving cost
  and acquisition date alone.
- Cent rounding is `ROUND_HALF_UP`, and the last lot of a disposal takes the
  remainder so no cent is lost to allocation.

An **open lot** has no disposal to classify, so the API derives its regime by
asking `regime_for` about an acquisition-shaped view of the lot: the asset class
and the account kind decide, exactly as they will for the sale that eventually
closes it. Only `p23` lots have a twelve-month clock, so only they show a
countdown in the UI.

### 3. Year summary (`tax/year_summary.py`)

`summarize(session, year, account_ids)` runs FIFO once over everything up to
31.12, which means the open lots it leaves behind *are* the end-of-year
holdings. It then buckets the year:

- **§ 23** — gains and losses split before Werbungskosten so that
  `net = gains − losses − werbungskosten = proceeds − cost − werbungskosten`,
  which is the shape Anlage SO wants. The 1.000 € Freigrenze is all-or-nothing.
- **§ 22 Nr. 3** — staking and airdrop income, 256 € Freigrenze, likewise
  all-or-nothing.
- **§ 20** — share gains/losses, Termin gains/losses, dividends, interest,
  creditable withholding tax, and `other_losses` (every loss that is not a share
  loss). Gains here are net of fees, unlike § 23.
- **InvStG** — one row per fund: distributions, Vorabpauschale, sale gains and
  losses, all **gross**. The Teilfreistellung percentage is reported for
  information; the Finanzamt applies it.
- **Vorabpauschale** per lot still held on 31.12, from the Basiszins, the
  values near 1.1. and 31.12., the distributions already received, and a
  month factor for lots bought during the year.
- **Venues** — what each venue would report for the year (gross proceeds,
  acquisitions, disposal count, deposits, withdrawals), for DAC8 reconciliation.

Every fact that could not be computed becomes a warning string on the summary
instead of a silent zero: a missing close, a missing Basiszins, a disposal with
no regime, an unlinked transfer, a pending FX value.

### 4. Forms and exports

`tax/forms.py` maps summary fields onto form lines for VZ 2025, each carrying
the date it was checked and a VERIFY note. `tax/anlage.py` renders a summary
through that map. `tax/exports.py` writes the ledger's own disposal and lot
CSVs plus Blockpit and CoinTracking import templates.

Every tax response carries `disclaimer` ("Berechnung — mit Steuerberater
prüfen") and `form_status` ("VZ 2025, geprüft 2026-09-06"), including as
headers on the CSV downloads. See `docs/tax-notes.md` for the underlying facts.

## Frontend

Vite + React 18 + TypeScript + Tailwind, built to `frontend/dist` and served by
the backend. `src/api/*.ts` mirrors the response models one file per router;
money stays a string until `fmt.ts` formats it. Charts are `lightweight-charts`
for price and `recharts` for everything else. `npm run check` runs a small
repo-specific helper check; `npm run build` type-checks with `tsc --noEmit`
first.

## Testing

`uv run pytest -q` — the engines and the tax module are tested as pure
functions against hand calculations; the routers through `TestClient` with an
in-memory SQLite per test; the adapters against recorded, **synthetic**
fixtures. No test reaches the network.
