"""Trading 212 API adapter: read-only order/dividend/transaction history plus
account cash and open positions, over httpx.

Doc verification (checked 2026-09-06) against the official OpenAPI bundle at
https://docs.trading212.com/_bundle/api.yaml (linked from
https://docs.trading212.com/api) and
https://helpcentre.trading212.com/hc/en-us/articles/14584770928157-Trading-212-API-key:

- **Auth** — HTTP Basic: username = API key, password = API secret
  (`securitySchemes.authWithSecretKey`, `scheme: basic`). Matches the brief.
  httpx's tuple `auth=` does exactly this, so no manual header building.
- **Base URLs** — `https://live.trading212.com` (live) /
  `https://demo.trading212.com` (paper/practice). Matches the brief.
- **Endpoint paths differ from the brief's shorthand** — the current spec has:
    * no `/equity/account/cash`; cash lives inside
      `GET /api/v0/equity/account/summary` (`AccountSummary.cash`:
      `availableToTrade`, `inPies`, `reservedForOrders`).
    * `/equity/portfolio` is actually `GET /api/v0/equity/positions`
      (array of `Position`).
    * `/history/dividends` / `/history/transactions` are nested under
      `/equity/...` like the orders endpoint:
      `/api/v0/equity/history/dividends`, `/api/v0/equity/history/transactions`.
  Implemented against these verified current paths.
- **Orders** (`/equity/history/orders`) — each item is
  `{fill: {id, price, quantity, filledAt, ...}, order: {id, ticker, side,
  quantity, filledQuantity, filledValue, currency, status, ...}}`. `fill` is
  only present once the order has actually executed; unfilled/cancelled
  orders (no `fill`) are skipped.
- **Dividends** (`/equity/history/dividends`) — `HistoryDividendItem` has no
  withholding-tax field in the current spec, so `withholding_tax_eur` is
  always 0 here. VERIFY: revisit if Trading 212 adds one.
- **Transactions** (`/equity/history/transactions`) — `type` enum is
  `WITHDRAW|DEPOSIT|FEE|TRANSFER|INTEREST_ON_FREE_CASH|LENDING_INTEREST`.
  Only the four kinds the brief names are mapped (deposit/withdrawal/fee/
  interest); `TRANSFER` has no `TxType` home yet and is skipped rather than
  guessed at — ponytail: add a mapping if a real transfer shows up.
- **Pagination** — `cursor` + `limit` (max 50) request params; the response
  carries `nextPagePath`, a ready-to-call relative path (not a bare cursor
  value) — following it verbatim is the documented way to page. A
  `_MAX_PAGES` cap guards against an API bug (or bad mock) turning that loop
  infinite. The transactions endpoint accepts a `time` filter per some
  sources, but it isn't in the verified OpenAPI bundle, so all three history
  endpoints filter by `since` client-side instead, consistently.
- **Rate limiting** — 429 with `x-ratelimit-reset` (Unix timestamp, seconds)
  is documented for every endpoint used here.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

import httpx

from ..enums import AssetClass, TxSource, TxType
from ..ledger import TxDraft

_LIVE_BASE = "https://live.trading212.com"
_DEMO_BASE = "https://demo.trading212.com"

_ORDERS_PATH = "/api/v0/equity/history/orders"
_DIVIDENDS_PATH = "/api/v0/equity/history/dividends"
_TRANSACTIONS_PATH = "/api/v0/equity/history/transactions"
_CASH_PATH = "/api/v0/equity/account/summary"
_POSITIONS_PATH = "/api/v0/equity/positions"
_INSTRUMENTS_PATH = "/api/v0/equity/metadata/instruments"

_MAX_RETRIES = 3
_MAX_PAGES = 500  # ponytail: a well-behaved API pages a handful of times; more is a bug, not more data

_TX_TYPE_MAP: dict[str, TxType] = {
    "DEPOSIT": TxType.DEPOSIT,
    "WITHDRAW": TxType.WITHDRAWAL,
    "FEE": TxType.FEE,
    "INTEREST_ON_FREE_CASH": TxType.INTEREST,
    "LENDING_INTEREST": TxType.INTEREST,
}

_ASSET_CLASS_MAP: dict[str, AssetClass] = {
    "STOCK": AssetClass.STOCK,
    "ETF": AssetClass.ETF,
    "CRYPTOCURRENCY": AssetClass.CRYPTO,
    "CRYPTO": AssetClass.CRYPTO,
}

# Ticker -> price-lookup symbol heuristic, per the brief: strip the venue
# suffix, lowercase the rest, append the market's Stooq-style domain.
_PRICE_SYMBOL_SUFFIXES = [("_US_EQ", ".us"), ("l_EQ", ".uk"), ("d_EQ", ".de")]


def _price_symbol(ticker: str) -> str | None:
    for suffix, domain in _PRICE_SYMBOL_SUFFIXES:
        if ticker.endswith(suffix):
            return f"{ticker[: -len(suffix)].lower()}{domain}"
    return None


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)  # 3.11+ parses a trailing "Z" natively


class Trading212Adapter:
    """Read-only client for one Trading 212 account (live or demo)."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        demo: bool = False,
        client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        base_url = _DEMO_BASE if demo else _LIVE_BASE
        self._client = client or httpx.Client(
            base_url=base_url, auth=(api_key, api_secret), timeout=30
        )
        self._sleep = sleep_fn
        self._instruments_cache: dict[str, dict] | None = None

    def close(self) -> None:
        self._client.close()

    # -- low-level HTTP -----------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        response = self._client.get(path, params=params)
        for _ in range(_MAX_RETRIES):
            if response.status_code != 429:
                break
            reset = response.headers.get("x-ratelimit-reset")
            sleep_for = max(0.0, float(reset) - time.time()) if reset else 1.0
            self._sleep(sleep_for)
            response = self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def _paginate(self, path: str, params: dict) -> list[dict]:
        items: list[dict] = []
        next_path: str | None = path
        next_params: dict | None = params
        for _ in range(_MAX_PAGES):
            if not next_path:
                return items
            payload = self._get(next_path, next_params)
            items.extend(payload.get("items", []))
            next_path = payload.get("nextPagePath") or None
            next_params = None  # nextPagePath already carries cursor/limit
        raise RuntimeError(f"pagination exceeded {_MAX_PAGES} pages for {path}")

    def _instruments(self) -> dict[str, dict]:
        if self._instruments_cache is None:
            raw = self._get(_INSTRUMENTS_PATH)
            self._instruments_cache = {item["ticker"]: item for item in raw}
        return self._instruments_cache

    def _asset_class(self, ticker: str) -> AssetClass:
        meta = self._instruments().get(ticker) or {}
        return _ASSET_CLASS_MAP.get(meta.get("type"), AssetClass.STOCK)

    # -- Adapter protocol -----------------------------------------------------

    def fetch_transactions(self, since: datetime | None) -> list[TxDraft]:
        drafts: list[TxDraft] = []
        drafts.extend(self._order_drafts(since))
        drafts.extend(self._dividend_drafts(since))
        drafts.extend(self._cash_tx_drafts(since))
        return drafts

    def fetch_balances(self) -> dict[str, Decimal]:
        summary = self._get(_CASH_PATH)
        cash = summary.get("cash") or {}
        balances: dict[str, Decimal] = {"EUR": _dec(cash.get("availableToTrade", 0))}
        for position in self._get(_POSITIONS_PATH):
            ticker = (position.get("instrument") or {}).get("ticker")
            if ticker:
                balances[ticker] = _dec(position.get("quantity", 0))
        return balances

    # -- mapping --------------------------------------------------------------

    def _order_drafts(self, since: datetime | None) -> list[TxDraft]:
        items = self._paginate(_ORDERS_PATH, {"limit": 50})
        drafts: list[TxDraft] = []
        for item in items:
            fill = item.get("fill")
            order = item.get("order") or {}
            if not fill:
                continue  # never executed — nothing to log
            filled_at = _parse_dt(fill["filledAt"])
            if since is not None and filled_at < since:
                continue

            ticker = order.get("ticker", "")
            quantity = abs(_dec(fill["quantity"]))
            price = _dec(fill["price"])
            currency = order.get("currency") or "EUR"
            filled_value = order.get("filledValue")
            fx_rate = order.get("fxRate")  # not in the current schema; kept for when T212 adds it

            if currency == "EUR":
                amount_eur = _dec(filled_value) if filled_value is not None else quantity * price
                fx_source = None
            elif fx_rate is not None:
                amount_eur = quantity * price * _dec(fx_rate)
                fx_source = "t212"
            else:
                amount_eur = Decimal(0)
                fx_source = "pending"

            instrument = order.get("instrument") or {}
            drafts.append(
                TxDraft(
                    ts=filled_at,
                    type=TxType.BUY if order.get("side") == "BUY" else TxType.SELL,
                    quantity=quantity,
                    price=price,
                    price_ccy=currency,
                    amount_eur=amount_eur,
                    fx_source=fx_source,
                    external_id=f"order:{order.get('id')}",
                    source=TxSource.API,
                    raw_json=json.dumps(item),
                    instrument_symbol=ticker or None,
                    asset_class=self._asset_class(ticker),
                    isin=instrument.get("isin"),
                    instrument_name=instrument.get("name"),
                    price_symbol=_price_symbol(ticker) if ticker else None,
                )
            )
        return drafts

    def _dividend_drafts(self, since: datetime | None) -> list[TxDraft]:
        items = self._paginate(_DIVIDENDS_PATH, {"limit": 50})
        drafts: list[TxDraft] = []
        for item in items:
            paid_on = _parse_dt(item["paidOn"])
            if since is not None and paid_on < since:
                continue
            ticker = item.get("ticker", "")
            instrument = item.get("instrument") or {}
            gross = item.get("grossAmountPerShare")
            quantity = item.get("quantity")
            drafts.append(
                TxDraft(
                    ts=paid_on,
                    type=TxType.DIVIDEND,
                    quantity=_dec(quantity) if quantity is not None else Decimal(0),
                    price=_dec(gross) if gross is not None else None,
                    price_ccy=item.get("tickerCurrency"),
                    amount_eur=_dec(item["amountInEuro"]),
                    # withholding_tax_eur left at 0 — see module docstring (VERIFY)
                    external_id=f"div:{item.get('reference')}",
                    source=TxSource.API,
                    raw_json=json.dumps(item),
                    instrument_symbol=ticker or None,
                    asset_class=self._asset_class(ticker),
                    isin=instrument.get("isin"),
                    instrument_name=instrument.get("name"),
                    price_symbol=_price_symbol(ticker) if ticker else None,
                )
            )
        return drafts

    def _cash_tx_drafts(self, since: datetime | None) -> list[TxDraft]:
        # `time` isn't a documented/verified query param for this endpoint —
        # filter client-side, same as orders/dividends above.
        items = self._paginate(_TRANSACTIONS_PATH, {"limit": 50})
        drafts: list[TxDraft] = []
        for item in items:
            ts = _parse_dt(item["dateTime"])
            if since is not None and ts < since:
                continue
            tx_type = _TX_TYPE_MAP.get(item.get("type"))
            if tx_type is None:
                continue  # e.g. TRANSFER — no TxType home yet, skip rather than guess
            currency = item.get("currency") or "EUR"
            amount = abs(_dec(item["amount"]))
            amount_eur = amount if currency == "EUR" else Decimal(0)
            fx_source = None if currency == "EUR" else "pending"
            drafts.append(
                TxDraft(
                    ts=ts,
                    type=tx_type,
                    amount_eur=amount_eur,
                    fx_source=fx_source,
                    external_id=f"tx:{item.get('reference')}",
                    source=TxSource.API,
                    raw_json=json.dumps(item),
                )
            )
        return drafts
