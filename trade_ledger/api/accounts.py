from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..enums import AccountKind, Mode, Venue
from ..ledger import cash_balance, positions
from ..models import Account, Instrument, SyncRun, Transaction
from ._common import get_account_or_404
from .schemas import BaseModel, Money

router = APIRouter()


class AccountIn(BaseModel):
    venue: Venue
    name: str
    kind: AccountKind
    mode: Mode
    base_ccy: str = "EUR"
    credential_env_prefix: str | None = None
    tax_wallet: str | None = None


class AccountOut(BaseModel):
    id: int
    venue: Venue
    name: str
    kind: AccountKind
    mode: Mode
    base_ccy: str
    credential_env_prefix: str | None
    tax_wallet: str
    active: bool
    created: datetime


class PositionOut(BaseModel):
    instrument: str
    quantity: Money


class AccountSummary(BaseModel):
    cash_eur: Money
    positions: list[PositionOut]
    tx_count: int
    last_sync: datetime | None


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(session: Session = Depends(get_session)):
    return session.execute(select(Account)).scalars().all()


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(payload: AccountIn, session: Session = Depends(get_session)):
    account = Account(**payload.model_dump())
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


@router.get("/accounts/{account_id}", response_model=AccountOut)
def get_account(account_id: int, session: Session = Depends(get_session)):
    return get_account_or_404(session, account_id)


@router.put("/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: int, payload: AccountIn, session: Session = Depends(get_session)):
    account = get_account_or_404(session, account_id)
    data = payload.model_dump()
    # `Account.__init__` defaults tax_wallet to name only on construction —
    # a setattr loop bypasses that, so re-apply the same default here.
    data["tax_wallet"] = data["tax_wallet"] or data["name"]
    for field, value in data.items():
        setattr(account, field, value)
    session.commit()
    session.refresh(account)
    return account


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(account_id: int, session: Session = Depends(get_session)):
    account = get_account_or_404(session, account_id)
    tx_count = session.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.account_id == account_id)
    )
    if tx_count:
        raise HTTPException(status_code=409, detail="account has transactions")
    session.delete(account)
    session.commit()


@router.get("/accounts/{account_id}/summary", response_model=AccountSummary)
def get_account_summary(account_id: int, session: Session = Depends(get_session)):
    get_account_or_404(session, account_id)

    cash_eur = cash_balance(session, account_id)
    position_out = [
        PositionOut(instrument=session.get(Instrument, instrument_id).symbol, quantity=qty)
        for instrument_id, qty in positions(session, [account_id]).items()
        if qty != 0
    ]
    tx_count = session.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.account_id == account_id)
    )
    last_sync = session.execute(
        select(SyncRun.started)
        .where(SyncRun.account_id == account_id)
        .order_by(SyncRun.started.desc())
        .limit(1)
    ).scalar_one_or_none()

    return AccountSummary(
        cash_eur=cash_eur, positions=position_out, tx_count=tx_count, last_sync=last_sync
    )
