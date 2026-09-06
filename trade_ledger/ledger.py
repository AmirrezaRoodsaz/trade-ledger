"""Ledger core: sign conventions, instrument dedup, transaction upsert, aggregates.

Pure functions over `Transaction`-shaped objects (ORM rows, or any object with
the same attributes) plus a couple of DB-backed helpers. See plan.md "Sign
conventions" for the source of truth `cash_delta_eur`/`position_delta` are
transcribed from.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .enums import AssetClass, TxSource, TxType
from .models import Account, Instrument, Transaction


def cash_delta_eur(tx) -> Decimal:
    t = tx.type
    if t == TxType.BUY:
        return -(tx.amount_eur + tx.fee_eur)
    if t == TxType.SELL:
        return tx.amount_eur - tx.fee_eur
    if t == TxType.DEPOSIT:
        return tx.amount_eur - tx.fee_eur
    if t == TxType.WITHDRAWAL:
        return -(tx.amount_eur + tx.fee_eur)
    if t in (TxType.DIVIDEND, TxType.INTEREST) or (
        t == TxType.STAKING_REWARD and tx.instrument_id is None
    ):
        return tx.amount_eur - tx.fee_eur - tx.withholding_tax_eur
    if t == TxType.FEE:
        return -tx.amount_eur
    if t == TxType.ADJUSTMENT:
        return tx.amount_eur
    return Decimal(0)  # transfers of instruments, in-kind rewards, split, vorabpauschale


def position_delta(tx) -> Decimal:
    if tx.instrument_id is None:
        return Decimal(0)
    if tx.type in (
        TxType.BUY,
        TxType.TRANSFER_IN,
        TxType.STAKING_REWARD,
        TxType.AIRDROP,
        TxType.SPLIT,
    ):
        return tx.quantity
    if tx.type in (TxType.SELL, TxType.TRANSFER_OUT):
        return -tx.quantity
    return Decimal(0)


def check_non_negative_amounts(
    type_: TxType, quantity: Decimal, fee_eur: Decimal, amount_eur: Decimal
) -> None:
    """`quantity`/`fee_eur` are always non-negative; `amount_eur` too, except
    `adjustment.amount_eur` which may be signed. Shared by `TxDraft` (every
    import path) and the manual-entry API's `TransactionIn` so both write
    paths enforce the same rule.
    """
    if quantity < 0:
        raise ValueError("quantity must be non-negative")
    if fee_eur < 0:
        raise ValueError("fee_eur must be non-negative")
    if amount_eur < 0 and type_ != TxType.ADJUSTMENT:
        raise ValueError(
            "amount_eur must be non-negative (only adjustment.amount_eur may be signed)"
        )


class TxDraft(BaseModel):
    """A `Transaction` not yet resolved against the DB: identifies its
    instrument by symbol/asset_class rather than an `instrument_id`.
    """

    ts: datetime
    type: TxType
    quantity: Decimal = Decimal(0)
    price: Decimal | None = None
    price_ccy: str | None = None
    fee: Decimal = Decimal(0)
    fee_ccy: str | None = None
    fx_rate: Decimal | None = None
    fx_source: str | None = None
    amount_eur: Decimal = Decimal(0)
    fee_eur: Decimal = Decimal(0)
    withholding_tax_eur: Decimal = Decimal(0)
    external_id: str | None = None
    link_id: str | None = None
    source: TxSource
    raw_json: str | None = None
    note: str | None = None
    instrument_symbol: str | None = None
    asset_class: AssetClass | None = None
    isin: str | None = None
    instrument_name: str | None = None
    price_symbol: str | None = None

    @model_validator(mode="after")
    def _check_signs(self) -> TxDraft:
        check_non_negative_amounts(self.type, self.quantity, self.fee_eur, self.amount_eur)
        return self


def get_or_create_instrument(
    session: Session,
    symbol: str,
    asset_class: AssetClass | str,
    isin: str | None = None,
    name: str | None = None,
    price_symbol: str | None = None,
    quote_ccy: str = "EUR",
) -> Instrument:
    """Match on `(symbol, asset_class)`; create with the given metadata if
    absent. On an existing row, backfill `isin`/`name`/`price_symbol` only
    where that field is still `None` — never overwrite a value someone (or
    an earlier import) already set. `price_source` is set to `"stooq"`
    whenever a `price_symbol` is newly set (create or backfill).
    """
    instrument = session.execute(
        select(Instrument).where(
            Instrument.symbol == symbol, Instrument.asset_class == asset_class
        )
    ).scalar_one_or_none()
    if instrument is not None:
        if isin is not None and instrument.isin is None:
            instrument.isin = isin
        if name is not None and instrument.name is None:
            instrument.name = name
        if price_symbol is not None and instrument.price_symbol is None:
            instrument.price_symbol = price_symbol
            instrument.price_source = "stooq"
        return instrument
    instrument = Instrument(
        symbol=symbol,
        asset_class=asset_class,
        isin=isin,
        name=name,
        price_symbol=price_symbol,
        price_source="stooq" if price_symbol else None,
        quote_ccy=quote_ccy,
    )
    session.add(instrument)
    session.flush()
    return instrument


def _hash_external_id(draft: TxDraft) -> str:
    symbol = draft.instrument_symbol or ""
    raw = f"{draft.ts.isoformat()}|{draft.type}|{symbol}|{draft.quantity}|{draft.amount_eur}"
    return "h:" + hashlib.sha1(raw.encode()).hexdigest()


def upsert_transactions(
    session: Session, account: Account, drafts: list[TxDraft]
) -> tuple[int, int]:
    """Resolve each draft's instrument and insert it unless `(account_id,
    external_id)` already exists (in the DB, or earlier in this same batch).
    Returns `(added, skipped)`.
    """
    added = 0
    skipped = 0
    seen_in_batch: set[str] = set()
    for draft in drafts:
        external_id = draft.external_id or _hash_external_id(draft)
        if external_id in seen_in_batch:
            skipped += 1
            continue
        exists = session.execute(
            select(Transaction.id).where(
                Transaction.account_id == account.id, Transaction.external_id == external_id
            )
        ).first()
        if exists is not None:
            skipped += 1
            continue
        seen_in_batch.add(external_id)

        instrument_id = None
        if draft.instrument_symbol is not None:
            instrument = get_or_create_instrument(
                session,
                draft.instrument_symbol,
                draft.asset_class,
                isin=draft.isin,
                name=draft.instrument_name,
                price_symbol=draft.price_symbol,
            )
            instrument_id = instrument.id

        session.add(
            Transaction(
                account_id=account.id,
                ts=draft.ts,
                type=draft.type,
                instrument_id=instrument_id,
                quantity=draft.quantity,
                price=draft.price,
                price_ccy=draft.price_ccy,
                fee=draft.fee,
                fee_ccy=draft.fee_ccy,
                fx_rate=draft.fx_rate,
                fx_source=draft.fx_source,
                amount_eur=draft.amount_eur,
                fee_eur=draft.fee_eur,
                withholding_tax_eur=draft.withholding_tax_eur,
                external_id=external_id,
                link_id=draft.link_id,
                source=draft.source,
                raw_json=draft.raw_json,
                note=draft.note,
            )
        )
        added += 1
    session.commit()
    return added, skipped


def positions(
    session: Session, account_ids: list[int], at: datetime | None = None
) -> dict[int, Decimal]:
    """Sum `position_delta` per instrument over transactions with `ts <= at`."""
    stmt = select(Transaction).where(Transaction.account_id.in_(account_ids))
    if at is not None:
        stmt = stmt.where(Transaction.ts <= at)
    result: dict[int, Decimal] = {}
    for tx in session.execute(stmt).scalars():
        if tx.instrument_id is None:
            continue
        result[tx.instrument_id] = result.get(tx.instrument_id, Decimal(0)) + position_delta(tx)
    return result


def cash_balance(session: Session, account_id: int, at: datetime | None = None) -> Decimal:
    """Sum `cash_delta_eur` over one account's transactions with `ts <= at`."""
    stmt = select(Transaction).where(Transaction.account_id == account_id)
    if at is not None:
        stmt = stmt.where(Transaction.ts <= at)
    return sum((cash_delta_eur(tx) for tx in session.execute(stmt).scalars()), Decimal(0))
