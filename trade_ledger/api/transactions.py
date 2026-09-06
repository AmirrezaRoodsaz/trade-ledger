from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..enums import TxSource, TxType
from ..models import Account, Instrument, Transaction
from .schemas import BaseModel, Money, Page

router = APIRouter()

_COLUMNS = [c.name for c in Transaction.__table__.columns]


class TransactionIn(BaseModel):
    account_id: int
    ts: datetime
    type: TxType
    instrument_id: int | None = None
    quantity: Money = Decimal(0)
    price: Money | None = None
    price_ccy: str | None = None
    fee: Money = Decimal(0)
    fee_ccy: str | None = None
    fx_rate: Money | None = None
    fx_source: str | None = None
    amount_eur: Money = Decimal(0)
    fee_eur: Money = Decimal(0)
    withholding_tax_eur: Money = Decimal(0)
    external_id: str | None = None
    link_id: str | None = None
    note: str | None = None


class TransactionOut(TransactionIn):
    id: int
    source: TxSource
    raw_json: str | None = None


def _apply_filters(
    stmt: Select,
    account_id: int | None,
    type: TxType | None,
    instrument_id: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> Select:
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    if type is not None:
        stmt = stmt.where(Transaction.type == type)
    if instrument_id is not None:
        stmt = stmt.where(Transaction.instrument_id == instrument_id)
    if date_from is not None:
        stmt = stmt.where(Transaction.ts >= date_from)
    if date_to is not None:
        stmt = stmt.where(Transaction.ts <= date_to)
    return stmt


def _get_account_or_404(session: Session, account_id: int) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    return account


@router.get("/transactions", response_model=Page[TransactionOut])
def list_transactions(
    account_id: int | None = None,
    type: TxType | None = None,
    instrument_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = 1,
    page_size: int = 100,
    session: Session = Depends(get_session),
):
    filtered = _apply_filters(
        select(Transaction), account_id, type, instrument_id, date_from, date_to
    )
    total = session.scalar(select(func.count()).select_from(filtered.subquery()))
    rows = (
        session.execute(
            filtered.order_by(Transaction.ts).offset((page - 1) * page_size).limit(page_size)
        )
        .scalars()
        .all()
    )
    return Page(items=rows, total=total)


@router.get("/transactions/export.csv")
def export_transactions_csv(
    account_id: int | None = None,
    type: TxType | None = None,
    instrument_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    session: Session = Depends(get_session),
):
    filtered = _apply_filters(
        select(Transaction), account_id, type, instrument_id, date_from, date_to
    ).order_by(Transaction.ts)
    rows = session.execute(filtered).scalars().all()

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(_COLUMNS)
        yield buffer.getvalue()
        for row in rows:
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow([getattr(row, column) for column in _COLUMNS])
            yield buffer.getvalue()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


@router.post("/transactions", response_model=TransactionOut, status_code=201)
def create_transaction(payload: TransactionIn, session: Session = Depends(get_session)):
    _get_account_or_404(session, payload.account_id)
    if payload.instrument_id is not None and session.get(Instrument, payload.instrument_id) is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    tx = Transaction(**payload.model_dump(), source=TxSource.MANUAL)
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return tx


@router.put("/transactions/{tx_id}", response_model=TransactionOut)
def update_transaction(tx_id: int, payload: TransactionIn, session: Session = Depends(get_session)):
    tx = session.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    _get_account_or_404(session, payload.account_id)
    if payload.instrument_id is not None and session.get(Instrument, payload.instrument_id) is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    for field, value in payload.model_dump().items():
        setattr(tx, field, value)
    session.commit()
    session.refresh(tx)
    return tx


@router.delete("/transactions/{tx_id}", status_code=204)
def delete_transaction(tx_id: int, session: Session = Depends(get_session)):
    tx = session.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    session.delete(tx)
    session.commit()
