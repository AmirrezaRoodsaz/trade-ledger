"""OKX trading-history CSV -> TxDraft rows.

OKX exports vary in the wild; two header sets are accepted:
- human: `Order ID, Instrument, Side, Avg Fill Price, Filled, Fee, Fee
  Currency, Trade Time, Trade ID`
- API-shaped: `id, instId, side, fillPx, fillSz, fee, feeCcy, ts, tradeId`
  (`ts` is a Unix-millisecond timestamp, matching the OKX REST API).

`Instrument`/`instId` (e.g. `BTC-EUR`) splits into symbol/quote. A non-EUR
quote means the EUR value can't be read off the row: `amount_eur` stays 0
and `fx_source="pending"` for the price service to fill in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from ..enums import AssetClass, TxSource, TxType
from ..ledger import TxDraft
from . import ImportResult, RowError
from ._common import dec, parse_utc, read_rows

_VARIANTS = [
    {
        "instrument": "Instrument",
        "side": "Side",
        "price": "Avg Fill Price",
        "size": "Filled",
        "fee": "Fee",
        "fee_ccy": "Fee Currency",
        "time": "Trade Time",
        "trade_id": "Trade ID",
    },
    {
        "instrument": "instId",
        "side": "side",
        "price": "fillPx",
        "size": "fillSz",
        "fee": "fee",
        "fee_ccy": "feeCcy",
        "time": "ts",
        "trade_id": "tradeId",
    },
]


def _pick_variant(fieldnames: list[str]) -> dict[str, str]:
    for variant in _VARIANTS:
        if variant["instrument"] in fieldnames:
            return variant
    raise ValueError(f"unrecognised OKX header: {fieldnames!r}")


def _parse_time(value: str, column: str) -> datetime:
    if column == "ts":
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    return parse_utc(value)


def parse(data: bytes) -> ImportResult:
    reader = read_rows(data)
    drafts: list[TxDraft] = []
    errors: list[RowError] = []

    try:
        variant = _pick_variant(reader.fieldnames or [])
    except ValueError as exc:
        return ImportResult(drafts=[], errors=[RowError(row=1, reason=str(exc))])

    for i, row in enumerate(reader, start=2):
        try:
            instrument = row[variant["instrument"]]
            symbol, _, quote_ccy = instrument.partition("-")
            quote_ccy = quote_ccy or "EUR"

            side = row[variant["side"]].strip().lower()
            if side == "buy":
                tx_type = TxType.BUY
            elif side == "sell":
                tx_type = TxType.SELL
            else:
                raise ValueError(f"unrecognised side: {side!r}")

            price = abs(dec(row[variant["price"]]))
            quantity = abs(dec(row[variant["size"]]))
            fee_raw = abs(dec(row[variant["fee"]]))
            fee_ccy = row[variant["fee_ccy"]] or None
            ts = _parse_time(row[variant["time"]], variant["time"])
            external_id = row[variant["trade_id"]] or None

            if quote_ccy == "EUR":
                amount_eur = price * quantity
                fx_source = None
            else:
                amount_eur = Decimal(0)
                fx_source = "pending"

            is_eur_fee = bool(fee_ccy) and fee_ccy.upper() == "EUR"
            fee_eur = fee_raw if is_eur_fee else Decimal(0)
            fee_kept = Decimal(0) if is_eur_fee else fee_raw
            fee_ccy_kept = None if is_eur_fee else fee_ccy

            drafts.append(
                TxDraft(
                    ts=ts,
                    type=tx_type,
                    quantity=quantity,
                    price=price,
                    price_ccy=quote_ccy,
                    fee=fee_kept,
                    fee_ccy=fee_ccy_kept,
                    fee_eur=fee_eur,
                    amount_eur=amount_eur,
                    fx_source=fx_source,
                    external_id=external_id,
                    source=TxSource.CSV,
                    instrument_symbol=symbol,
                    asset_class=AssetClass.CRYPTO,
                )
            )
        except (KeyError, ValueError, ArithmeticError) as exc:
            errors.append(RowError(row=i, reason=str(exc)))

    return ImportResult(drafts=drafts, errors=errors)
