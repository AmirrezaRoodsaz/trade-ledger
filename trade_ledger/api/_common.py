"""Small helpers shared by more than one router. Not a router itself — the
leading underscore also keeps `api.ROUTERS` auto-discovery from mistaking it
for one (it has no `router` attribute anyway, but the name makes intent clear).
"""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, Instrument

ModeFilter = Literal["live", "paper", "demo", "all"]


def get_account_or_404(session: Session, account_id: int) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    return account


def get_instrument_or_404(session: Session, instrument_id: int) -> Instrument:
    instrument = session.get(Instrument, instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    return instrument


def resolve_account_ids(session: Session, account_id: list[int] | None, mode: str) -> list[int]:
    """Which accounts a stats endpoint reads. Explicit `account_id` values win
    (404 if one is unknown); otherwise every account in `mode`, and `all`
    means no mode filter. Modes are never mixed unless asked for.
    """
    if account_id:
        return [get_account_or_404(session, one).id for one in account_id]
    stmt = select(Account.id)
    if mode != "all":
        stmt = stmt.where(Account.mode == mode)
    return list(session.execute(stmt).scalars())
