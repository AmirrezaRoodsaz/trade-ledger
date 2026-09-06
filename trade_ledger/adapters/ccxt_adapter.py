"""CCXT-backed adapter for OKX and Kraken: read-only trade/deposit/withdrawal/
staking history plus balances, over the `ccxt` library.

Read-only surface: only `fetch_my_trades`, `fetch_ledger`, `fetch_deposits`,
`fetch_withdrawals`, `fetch_balance` are ever called — nothing here places,
cancels, or withdraws.

Mapping notes:
- Trades: base/quote come from the unified `"BASE/QUOTE"` symbol.
  `amount_eur` = `cost` when the quote is EUR; otherwise pending (0 +
  `fx_source="pending"`). Same idea for the fee leg, in its own currency —
  either leg being non-EUR marks the whole draft `fx_source="pending"`
  (`TxDraft` has one fx_source field, not one per leg).
- Deposits/withdrawals: fiat currencies (EUR/USD/GBP/CHF) become
  DEPOSIT/WITHDRAWAL; anything else is a crypto TRANSFER_IN/TRANSFER_OUT
  with the instrument set.
- Staking: `fetch_ledger` entries are only used for staking rewards (unified
  `type == "staking"`, or Kraken's raw `info.subtype == "reward"` when ccxt
  doesn't normalise it). Trade/deposit/withdrawal ledger entries are
  ignored here — those already come from the three calls above.
  # ponytail: the plan sketched using Kraken's ledger as the sole source of
  # truth (joining back to fetch_my_trades for price). That needs an
  # id-matching join with no test coverage calling for it; this adapter
  # instead treats deposits/withdrawals/trades uniformly across both
  # venues and only mines the ledger for staking. Upgrade to the join if
  # Kraken sync turns out to miss entries the simple calls don't surface.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import ccxt

from ..enums import AssetClass, TxSource, TxType
from ..ledger import TxDraft

_PAGE_LIMIT = 100
_MAX_PAGES = 200
_FIAT_CCYS = {"EUR", "USD", "GBP", "CHF"}
_OKX_WINDOW_DAYS = 85
_OKX_WINDOW_WARNING = (
    "OKX API only returns 3 months; import the quarterly archive CSV for older data"
)


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _ts(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


class CcxtAdapter:
    """Read-only client for one OKX or Kraken account, via `ccxt`."""

    def __init__(
        self,
        exchange_id: str,
        key: str,
        secret: str,
        passphrase: str | None = None,
        demo: bool = False,
        exchange: Any = None,
    ) -> None:
        if demo and exchange_id == "kraken":
            raise NotImplementedError("kraken has no demo mode")

        self._exchange_id = exchange_id
        self.warnings: list[str] = []

        if exchange is not None:
            self._exchange = exchange
        else:
            config: dict[str, Any] = {
                "apiKey": key,
                "secret": secret,
                "enableRateLimit": True,
            }
            if passphrase:
                config["password"] = passphrase
            self._exchange = getattr(ccxt, exchange_id)(config)

        if demo:
            self._exchange.set_sandbox_mode(True)

    def close(self) -> None:
        close = getattr(self._exchange, "close", None)
        if callable(close):
            close()

    # -- pagination -----------------------------------------------------------

    def _paginate(
        self, fetch: Callable[..., list[dict]], since_ms: int | None, kind: str
    ) -> list[dict]:
        """Page through `fetch(since=, limit=)`. Every item without a
        `timestamp` (pending deposits/withdrawals on OKX/Kraken routinely
        have `None` there) is dropped right here — the single choke point
        every trade/deposit/withdrawal/ledger call passes through — instead
        of crashing `max()` below or the `_ts()` conversion further downstream.
        A warning records what was skipped so a sync run still finishes ok.
        """
        items: list[dict] = []
        cursor = since_ms
        newest = None
        for _ in range(_MAX_PAGES):
            batch = fetch(since=cursor, limit=_PAGE_LIMIT)
            if not batch:
                break
            timed = []
            for item in batch:
                if item.get("timestamp") is None:
                    self.warnings.append(
                        f"{kind} {item.get('id')} skipped: no timestamp (pending?)"
                    )
                    continue
                timed.append(item)
            items.extend(timed)
            if not timed:
                break  # nothing left to anchor the next page's cursor on
            batch_newest = max(item["timestamp"] for item in timed)
            if len(batch) < _PAGE_LIMIT or (newest is not None and batch_newest <= newest):
                break
            newest = batch_newest
            cursor = batch_newest + 1
        return items

    # -- Adapter protocol -------------------------------------------------------

    def fetch_transactions(self, since: datetime | None) -> list[TxDraft]:
        self.warnings = []
        if self._exchange_id == "okx":
            cutoff = datetime.now(UTC) - timedelta(days=_OKX_WINDOW_DAYS)
            if since is None or since < cutoff:
                self.warnings.append(_OKX_WINDOW_WARNING)

        since_ms = int(since.timestamp() * 1000) if since is not None else None
        drafts: list[TxDraft] = []
        for trade in self._paginate(self._exchange.fetch_my_trades, since_ms, "trade"):
            drafts.append(self._trade_draft(trade))
        for dep in self._paginate(self._exchange.fetch_deposits, since_ms, "deposit"):
            drafts.append(self._transfer_draft(dep, "in"))
        for wd in self._paginate(self._exchange.fetch_withdrawals, since_ms, "withdrawal"):
            drafts.append(self._transfer_draft(wd, "out"))
        for entry in self._paginate(self._exchange.fetch_ledger, since_ms, "ledger entry"):
            staking = self._staking_draft(entry)
            if staking is not None:
                drafts.append(staking)
        return drafts

    def fetch_balances(self) -> dict[str, Decimal]:
        raw = self._exchange.fetch_balance()
        totals = raw.get("total") or {}
        return {ccy: _dec(amount) for ccy, amount in totals.items()}

    # -- mapping ------------------------------------------------------------

    def _trade_draft(self, trade: dict) -> TxDraft:
        base, _, quote = trade["symbol"].partition("/")
        quantity = abs(_dec(trade["amount"]))
        price = _dec(trade["price"]) if trade.get("price") is not None else None
        if trade.get("cost") is not None:
            cost = _dec(trade["cost"])
        else:
            cost = quantity * (price or Decimal(0))

        pending = False
        if quote == "EUR":
            amount_eur = cost
        else:
            amount_eur = Decimal(0)
            pending = True

        fee_info = trade.get("fee") or {}
        fee_cost = fee_info.get("cost")
        fee_ccy = fee_info.get("currency")
        fee_dec = _dec(fee_cost) if fee_cost is not None else Decimal(0)
        if fee_cost is None:
            fee_eur = Decimal(0)
        elif fee_ccy == "EUR":
            fee_eur = fee_dec
        else:
            fee_eur = Decimal(0)
            pending = True

        return TxDraft(
            ts=_ts(trade["timestamp"]),
            type=TxType.BUY if trade.get("side") == "buy" else TxType.SELL,
            quantity=quantity,
            price=price,
            price_ccy=quote or None,
            fee=fee_dec,
            fee_ccy=fee_ccy,
            amount_eur=amount_eur,
            fee_eur=fee_eur,
            fx_source="pending" if pending else None,
            external_id=f"trade:{trade['id']}",
            source=TxSource.API,
            raw_json=json.dumps(trade),
            instrument_symbol=base or None,
            asset_class=AssetClass.CRYPTO,
        )

    def _transfer_draft(self, item: dict, direction: str) -> TxDraft:
        currency = item["currency"]
        amount = abs(_dec(item["amount"]))
        fee_info = item.get("fee") or {}
        fee_cost = fee_info.get("cost")
        fee_ccy = fee_info.get("currency")
        fee_dec = _dec(fee_cost) if fee_cost is not None else Decimal(0)
        ext_prefix = "dep" if direction == "in" else "wd"

        if currency in _FIAT_CCYS:
            tx_type = TxType.DEPOSIT if direction == "in" else TxType.WITHDRAWAL
            if currency == "EUR":
                amount_eur = amount
                fee_eur = fee_dec if fee_ccy == "EUR" else Decimal(0)
                fx_source = None
            else:
                amount_eur = Decimal(0)
                fee_eur = Decimal(0)
                fx_source = "pending"
            instrument_symbol = None
            asset_class = None
            quantity = Decimal(0)
        else:
            tx_type = TxType.TRANSFER_IN if direction == "in" else TxType.TRANSFER_OUT
            amount_eur = Decimal(0)
            fee_eur = Decimal(0)
            fx_source = None
            instrument_symbol = currency
            asset_class = AssetClass.CRYPTO
            quantity = amount

        return TxDraft(
            ts=_ts(item["timestamp"]),
            type=tx_type,
            quantity=quantity,
            fee=fee_dec,
            fee_ccy=fee_ccy,
            amount_eur=amount_eur,
            fee_eur=fee_eur,
            fx_source=fx_source,
            external_id=f"{ext_prefix}:{item['id']}",
            source=TxSource.API,
            raw_json=json.dumps(item),
            instrument_symbol=instrument_symbol,
            asset_class=asset_class,
        )

    def _staking_draft(self, entry: dict) -> TxDraft | None:
        is_staking = (
            entry.get("type") == "staking" or (entry.get("info") or {}).get("subtype") == "reward"
        )
        if not is_staking:
            return None

        currency = entry.get("currency")
        amount = abs(_dec(entry.get("amount", 0)))
        if currency == "EUR":
            amount_eur = amount
            fx_source = None
            instrument_symbol = None
            quantity = Decimal(0)
        else:
            amount_eur = Decimal(0)
            fx_source = "pending"
            instrument_symbol = currency
            quantity = amount

        return TxDraft(
            ts=_ts(entry["timestamp"]),
            type=TxType.STAKING_REWARD,
            quantity=quantity,
            amount_eur=amount_eur,
            fx_source=fx_source,
            external_id=f"ledger:{entry['id']}",
            source=TxSource.API,
            raw_json=json.dumps(entry),
            instrument_symbol=instrument_symbol,
            asset_class=AssetClass.CRYPTO if instrument_symbol else None,
        )
