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
    """Which accounts a stats endpoint reads.

    Modes are never mixed. `mode` filters the result even when `account_id`
    names accounts explicitly, so an id belonging to another mode yields no
    accounts rather than leaking a live position into a paper figure — the
    same rule `trades._mode_filter` applies. UI account tabs pass `mode=all`,
    which uses the given ids as-is.

    An id that matches no account at all is still a 404: a typo should not
    read as an empty portfolio.
    """
    ids = [get_account_or_404(session, one).id for one in account_id or []]
    if mode == "all":
        return ids or list(session.execute(select(Account.id)).scalars())
    stmt = select(Account.id).where(Account.mode == mode)
    if ids:
        stmt = stmt.where(Account.id.in_(ids))
    return list(session.execute(stmt).scalars())
