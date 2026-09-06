# Venue setup

How to give trade-ledger read access to each venue, which `.env` keys to fill,
and what the sync actually covers.

> **Read-only, always.** Nothing in this repo places, cancels, modifies or
> withdraws. Create keys with read/query permission only — then a leaked key
> costs you your trade history, not your money.
>
> Menu paths below were checked 2026-09-06. Venues move their settings around;
> if a path no longer matches, look for "API" under account or security
> settings.

Copy `.env.example` to `.env` first. `.env` is gitignored and must stay that
way. Keys are read from the process environment first and from `.env` second,
so a key exported in your shell wins over the file.

Each account row in the app carries a `credential_env_prefix` (for example
`T212_LIVE`). The adapters then read `{prefix}_API_KEY`, `{prefix}_API_SECRET`
and, for OKX, `{prefix}_API_PASSPHRASE`. Add a prefix, add the matching keys,
and a new account works without touching code.

---

## Trading 212

**Create the key** — in the mobile app: *Settings → API (Beta) → Generate API
key*. Grant read scopes only. Switch the app to the **Practice** account first
if you want a demo key; live and practice keys are separate and are not
interchangeable.

**Fill in**

| Account | Keys |
|---|---|
| Live (Invest / ISA) | `T212_LIVE_API_KEY`, `T212_LIVE_API_SECRET` |
| Practice | `T212_DEMO_API_KEY`, `T212_DEMO_API_SECRET` |

The adapter authenticates with HTTP Basic (key as username, secret as
password). If your key has no separate secret, put the key in both fields.

**Sync covers** order fills (buys and sells), dividends, cash transactions
(deposits, withdrawals, fees, interest on free cash), account cash and open
positions.

**Limits**

- Invest and ISA accounts only. **CFD history is not in the API** — export it
  and import the CSV.
- The dividend endpoint carries no withholding-tax field, so
  `withholding_tax_eur` stays 0 until you enter it by hand.
- Transactions of type `TRANSFER` have no ledger equivalent and are skipped
  rather than guessed at.
- The API rate-limits with HTTP 429 and an `x-ratelimit-reset` header; the
  adapter waits and retries.

---

## OKX

**Create the key** — web: *Profile → API → Create V5 API key*. Permission:
**Read** only (never Trade, never Withdraw). You choose a **passphrase** while
creating the key — it is a third credential, not your login password, and it
cannot be recovered later. Demo trading keys are created from inside demo
trading and are separate from live keys.

**Fill in**

| Account | Keys |
|---|---|
| Live | `OKX_LIVE_API_KEY`, `OKX_LIVE_API_SECRET`, `OKX_LIVE_API_PASSPHRASE` |
| Demo | `OKX_DEMO_API_KEY`, `OKX_DEMO_API_SECRET`, `OKX_DEMO_API_PASSPHRASE` |

**Sync covers** spot trades, deposits, withdrawals, staking rewards and
balances, through `ccxt`.

**Limits**

- **Roughly three months of history.** The API window is the hard limit, not the
  adapter's; a sync that reaches it records the warning *"OKX API only returns
  3 months; import the quarterly archive CSV for older data"* on the sync run.
  Do the first import from CSV and let the API keep it current from there.
- Non-EUR quote pairs arrive with `fx_source="pending"` and get their EUR value
  once ECB rates are fetched (`trade-ledger prices --refresh`).
- If the key was created with an IP allowlist, the machine running the app must
  be on it.

---

## Kraken

**Create the key** — web: *Settings → API → Add key*. Tick **Query** permissions
only (query funds, query ledger entries, query open/closed orders and trades).
Leave every trade and withdraw permission off.

**Fill in**: `KRAKEN_LIVE_API_KEY`, `KRAKEN_LIVE_API_SECRET`.

**Sync covers** trades, deposits, withdrawals, staking/earn rewards and
balances, through `ccxt`.

**Limits**

- Kraken has **no sandbox**. A Kraken account marked `paper` or `demo` in the
  app still talks to the live read-only API — the mode means "do not trade on
  it", not "hit a test server".
- Staking rewards are read out of the ledger endpoint; ordinary trade and
  transfer ledger rows are ignored there because the dedicated endpoints
  already return them.

---

## CSV fallbacks

Every venue's CSV export can be imported from *Account → Import*, or from the
CLI:

```bash
uv run trade-ledger import --account "okx-live" --format okx export.csv
```

The import previews the parsed rows and the errors before anything is written,
and the commit is idempotent — re-importing an overlapping export adds nothing
twice.

| `--format` | Expected export |
|---|---|
| `trading212` | Trading 212 *History → Export*, the full column set including `Action`, `Time`, `ISIN`, `Ticker`, `Total`, `Withholding tax` |
| `okx` | OKX trading history, either the human export (`Order ID, Instrument, Side, Avg Fill Price, …`) or the API-shaped one (`id, instId, side, fillPx, …`). The **quarterly archive** is the way to get history older than the API window |
| `kraken` | Kraken ledger history (`txid, refid, time, type, subtype, aclass, asset, amount, fee, balance`) |
| `binance` | Binance transaction history (`User_ID, UTC_Time, Account, Operation, Coin, Change, Remark`) — there is no Binance API adapter, CSV is the only route |
| `generic` | Anything else: `date, type, symbol, asset_class, quantity, price, currency, fee, fee_currency, amount_eur, external_id, note`. Also the route for **Interactive Brokers** — convert a Flex Query export into these columns |

CSV rows that carry no EUR value are stored with `fx_source="pending"` and
resolved by `trade-ledger prices --refresh` against ECB reference rates.
