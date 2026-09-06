"""Kraken ledger-history CSV -> TxDraft rows.

Header: `txid, refid, time, type, subtype, aclass, asset, amount, fee,
balance`. Rows sharing a `refid` belong to one ledger event: a `trade` is a
negative + a positive leg (one fiat, one asset); `deposit`/`withdrawal` and
`staking`/`earn` rows are usually solo.

Asset codes are normalised (`XXBT`/`XBT`->BTC, `XETH`->ETH, `ZEUR`->EUR,
and generally a leading X/Z is stripped off any 4-character code) because
Kraken's ledger uses its own ISO-ish codes rather than plain tickers.
"""

from __future__ import annotations

from decimal import Decimal

from ..enums import AssetClass, TxSource, TxType
from ..ledger import TxDraft
from . import ImportResult, RowError
from ._common import dec, parse_utc, read_rows

_ASSET_MAP = {"XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "ZEUR": "EUR", "ZUSD": "USD"}
_FIAT_LEG_CODES = {"ZEUR", "EUR", "ZUSD", "USD"}


def _normalize_asset(code: str) -> str:
    code = code.strip()
    if code in _ASSET_MAP:
        return _ASSET_MAP[code]
    if len(code) == 4 and code[0] in ("X", "Z"):
        return code[1:]
    return code


def _trade_draft(rows: list[dict], refid: str) -> TxDraft:
    if len(rows) != 2:
        raise ValueError(f"expected 2 legs for trade {refid!r}, got {len(rows)}")
    leg_a, leg_b = rows
    if leg_a["asset"].strip().upper() in _FIAT_LEG_CODES:
        fiat_leg, asset_leg = leg_a, leg_b
    elif leg_b["asset"].strip().upper() in _FIAT_LEG_CODES:
        fiat_leg, asset_leg = leg_b, leg_a
    else:
        raise ValueError(f"no fiat leg for trade {refid!r}")

    fiat_amount = dec(fiat_leg["amount"])
    asset_amount = dec(asset_leg["amount"])
    fiat_ccy = _normalize_asset(fiat_leg["asset"])
    symbol = _normalize_asset(asset_leg["asset"])
    fiat_fee = abs(dec(fiat_leg["fee"]))
    asset_fee = abs(dec(asset_leg["fee"]))
    tx_type = TxType.BUY if asset_amount > 0 else TxType.SELL
    ts = parse_utc(fiat_leg["time"])

    fee_eur = Decimal(0)
    fee = Decimal(0)
    fee_ccy = None
    fx_source = None

    if fiat_ccy == "EUR":
        amount_eur = abs(fiat_amount)
        fee_eur = fiat_fee
    else:
        amount_eur = Decimal(0)
        fx_source = "pending"
        if fiat_fee:
            fee, fee_ccy = fiat_fee, fiat_ccy

    # ponytail: a TxDraft has one fee slot (fee/fee_ccy). The fiat fee already
    # has a home (fee_eur when EUR, else this slot) so the asset-leg fee
    # (its own coin, e.g. BTC) only takes the slot if nothing else claimed
    # it. Upgrade to a multi-fee model if a venue routinely charges both legs.
    if asset_fee and not fee:
        fee, fee_ccy, fx_source = asset_fee, symbol, "pending"

    return TxDraft(
        ts=ts,
        type=tx_type,
        quantity=abs(asset_amount),
        fee=fee,
        fee_ccy=fee_ccy,
        fee_eur=fee_eur,
        amount_eur=amount_eur,
        fx_source=fx_source,
        external_id=refid,
        source=TxSource.CSV,
        instrument_symbol=symbol,
        asset_class=AssetClass.CRYPTO,
    )


def _transfer_draft(row: dict, refid: str, kind: str) -> TxDraft:
    asset = _normalize_asset(row["asset"])
    amount = dec(row["amount"])
    fee = abs(dec(row["fee"]))
    ts = parse_utc(row["time"])
    is_fiat = asset == "EUR"

    if kind == "deposit":
        tx_type = TxType.DEPOSIT if is_fiat else TxType.TRANSFER_IN
    else:
        tx_type = TxType.WITHDRAWAL if is_fiat else TxType.TRANSFER_OUT

    if is_fiat:
        return TxDraft(
            ts=ts, type=tx_type, amount_eur=abs(amount), fee_eur=fee,
            external_id=refid, source=TxSource.CSV,
        )
    return TxDraft(
        ts=ts, type=tx_type, quantity=abs(amount), fee_eur=fee,
        external_id=refid, source=TxSource.CSV,
        instrument_symbol=asset, asset_class=AssetClass.CRYPTO,
    )


def _reward_draft(row: dict, refid: str) -> TxDraft:
    asset = _normalize_asset(row["asset"])
    amount = dec(row["amount"])
    ts = parse_utc(row["time"])
    return TxDraft(
        ts=ts,
        type=TxType.STAKING_REWARD,
        quantity=abs(amount),
        amount_eur=Decimal(0),
        fx_source="pending",  # no price in a ledger export; Task 5 fills it
        external_id=refid,
        source=TxSource.CSV,
        instrument_symbol=asset,
        asset_class=AssetClass.CRYPTO,
    )


def parse(data: bytes) -> ImportResult:
    groups: dict[str, list[tuple[int, dict]]] = {}
    for row_num, row in enumerate(read_rows(data), start=2):
        groups.setdefault(row["refid"], []).append((row_num, row))

    drafts: list[TxDraft] = []
    errors: list[RowError] = []

    for refid, entries in groups.items():
        first_row = entries[0][0]
        rows = [r for _, r in entries]
        try:
            kinds = {r["type"].strip() for r in rows}
            if len(kinds) != 1:
                raise ValueError(f"mixed ledger types for refid {refid!r}: {kinds!r}")
            kind = kinds.pop()

            if kind == "trade":
                drafts.append(_trade_draft(rows, refid))
            elif kind in ("deposit", "withdrawal"):
                drafts.append(_transfer_draft(rows[0], refid, kind))
            elif kind in ("staking", "earn"):
                drafts.append(_reward_draft(rows[0], refid))
            else:
                raise ValueError(f"unrecognised kraken ledger type: {kind!r}")
        except (KeyError, ValueError, ArithmeticError) as exc:
            errors.append(RowError(row=first_row, reason=str(exc)))

    return ImportResult(drafts=drafts, errors=errors)
