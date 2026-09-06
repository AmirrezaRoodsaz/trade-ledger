"""Small helpers shared by more than one router. Not a router itself — the
leading underscore also keeps `api.ROUTERS` auto-discovery from mistaking it
for one (it has no `router` attribute anyway, but the name makes intent clear).
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Account, Instrument


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
