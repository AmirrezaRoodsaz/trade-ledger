"""Generic CSV -> TxDraft rows: an escape hatch for anything not covered by
a venue-specific importer. Header: `date, type, symbol, asset_class,
quantity, price, currency, fee, fee_currency, amount_eur, external_id,
note`. `type`/`asset_class` are `TxType`/`AssetClass` names (e.g. "buy",
"crypto"); `amount_eur` is already the EUR value, no conversion needed.
"""

from __future__ import annotations

from decimal import Decimal

from ..enums import AssetClass, TxSource, TxType
from ..ledger import TxDraft
from . import ImportResult, RowError
from ._common import dec, parse_utc, read_rows


def parse(data: bytes) -> ImportResult:
    drafts: list[TxDraft] = []
    errors: list[RowError] = []

    for i, row in enumerate(read_rows(data), start=2):
        try:
            tx_type = TxType(row["type"].strip())
            ts = parse_utc(row["date"])
            symbol = row.get("symbol") or None
            asset_class = AssetClass(row["asset_class"].strip()) if row.get("asset_class") else None
            quantity = abs(dec(row.get("quantity")))
            price = dec(row.get("price")) or None
            price_ccy = row.get("currency") or None
            fee = abs(dec(row.get("fee")))
            fee_ccy = row.get("fee_currency") or None
            amount_eur = dec(row.get("amount_eur"))
            external_id = row.get("external_id") or None
            note = row.get("note") or None

            is_eur_fee = not fee_ccy or fee_ccy.upper() == "EUR"
            fee_eur = fee if is_eur_fee else Decimal(0)
            fee_kept = Decimal(0) if is_eur_fee else fee

            drafts.append(
                TxDraft(
                    ts=ts,
                    type=tx_type,
                    quantity=quantity,
                    price=price,
                    price_ccy=price_ccy,
                    fee=fee_kept,
                    fee_ccy=fee_ccy,
                    fee_eur=fee_eur,
                    amount_eur=amount_eur,
                    external_id=external_id,
                    source=TxSource.CSV,
                    note=note,
                    instrument_symbol=symbol,
                    asset_class=asset_class,
                )
            )
        except (KeyError, ValueError, ArithmeticError) as exc:
            errors.append(RowError(row=i, reason=str(exc)))

    return ImportResult(drafts=drafts, errors=errors)
