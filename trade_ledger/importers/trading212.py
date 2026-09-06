"""Trading 212 CSV export -> TxDraft rows.

Header: `Action, Time, ISIN, Ticker, Name, No. of shares, Price / share,
Currency (Price / share), Exchange rate, Result, Currency (Result), Total,
Currency (Total), Withholding tax, Currency (Withholding tax), Stamp duty
reserve tax, Currency conversion fee, Notes, ID`.

Total/withholding tax are in the account's base currency unless their
`Currency (...)` column says otherwise, in which case `Exchange rate`
converts them; without a rate the EUR value can't be determined and the row
is marked `fx_source="pending"` per the global CSV-import contract.
"""

from __future__ import annotations

from decimal import Decimal

from ..enums import AssetClass, TxSource, TxType
from ..ledger import TxDraft
from . import ImportResult, RowError
from ._common import dec, parse_utc, read_rows

_INSTRUMENT_TYPES = (TxType.BUY, TxType.SELL, TxType.DIVIDEND)


def _tx_type(action: str) -> TxType | None:
    action = action.strip()
    if action in ("Market buy", "Limit buy", "Stop buy"):
        return TxType.BUY
    if action.endswith("sell"):
        return TxType.SELL
    if action.startswith("Dividend"):
        return TxType.DIVIDEND
    if action == "Deposit":
        return TxType.DEPOSIT
    if action == "Withdrawal":
        return TxType.WITHDRAWAL
    if action == "Interest on cash":
        return TxType.INTEREST
    if action == "Currency conversion fee":
        return TxType.FEE
    return None


def parse(data: bytes) -> ImportResult:
    drafts: list[TxDraft] = []
    errors: list[RowError] = []

    for i, row in enumerate(read_rows(data), start=2):
        try:
            action = row["Action"]
            tx_type = _tx_type(action)
            if tx_type is None:
                raise ValueError(f"unrecognised action: {action!r}")

            ts = parse_utc(row["Time"])
            total = dec(row.get("Total"))
            total_ccy = row.get("Currency (Total)") or "EUR"
            fx_rate = dec(row.get("Exchange rate")) or None
            conversion_fee = dec(row.get("Currency conversion fee"))
            external_id = row.get("ID") or None

            fx_source = None
            if total_ccy == "EUR":
                amount_eur = abs(total)
            elif fx_rate:
                amount_eur = abs(total) / fx_rate
            else:
                amount_eur = Decimal(0)
                fx_source = "pending"

            instrument_symbol = None
            asset_class = None
            isin = None
            price = None
            price_ccy = None
            quantity = Decimal(0)
            withholding_tax_eur = Decimal(0)

            if tx_type in _INSTRUMENT_TYPES:
                instrument_symbol = row.get("Ticker") or None
                isin = row.get("ISIN") or None
                name = (row.get("Name") or "").upper()
                asset_class = (
                    AssetClass.ETF if ("ETF" in name or "UCITS" in name) else AssetClass.STOCK
                )
                price = dec(row.get("Price / share")) or None
                price_ccy = row.get("Currency (Price / share)") or None
                quantity = abs(dec(row.get("No. of shares")))

            if tx_type == TxType.DIVIDEND:
                withholding_raw = dec(row.get("Withholding tax"))
                withholding_ccy = row.get("Currency (Withholding tax)") or "EUR"
                if withholding_ccy != "EUR" and fx_rate:
                    withholding_tax_eur = withholding_raw / fx_rate
                else:
                    withholding_tax_eur = withholding_raw

            # "Currency conversion fee" as its own Action row has no trade to
            # attach to: the row's own amount *is* the fee (ponytail: folded
            # here rather than modelled as amount_eur, per the brief).
            fee_eur = conversion_fee
            row_amount_eur = amount_eur
            if tx_type == TxType.FEE:
                fee_eur = amount_eur
                row_amount_eur = Decimal(0)
                fx_source = None

            drafts.append(
                TxDraft(
                    ts=ts,
                    type=tx_type,
                    quantity=quantity,
                    price=price,
                    price_ccy=price_ccy,
                    fee_eur=fee_eur,
                    fx_rate=fx_rate,
                    fx_source=fx_source,
                    amount_eur=row_amount_eur,
                    withholding_tax_eur=withholding_tax_eur,
                    external_id=external_id,
                    source=TxSource.CSV,
                    instrument_symbol=instrument_symbol,
                    asset_class=asset_class,
                    isin=isin,
                )
            )
        except (KeyError, ValueError, ArithmeticError) as exc:
            errors.append(RowError(row=i, reason=str(exc)))

    return ImportResult(drafts=drafts, errors=errors)
