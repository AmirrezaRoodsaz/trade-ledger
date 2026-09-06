from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters.base import sync_account
from ..db import get_session
from ..models import SyncRun
from ..settings import Settings, get_settings
from ._common import get_account_or_404
from .schemas import BaseModel

router = APIRouter()


class SyncRunOut(BaseModel):
    id: int
    account_id: int
    started: datetime
    finished: datetime | None
    status: str
    added: int
    skipped: int
    error: str | None
    # Pending-EUR rows this run resolved. Not a `SyncRun` column — `sync_account`
    # sets it on the returned instance; an errored run never gets there, hence 0.
    filled_pending: int = 0


@router.post("/accounts/{account_id}/sync", response_model=SyncRunOut)
def trigger_sync(
    account_id: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    account = get_account_or_404(session, account_id)
    return sync_account(session, account, settings)


@router.get("/accounts/{account_id}/sync-runs", response_model=list[SyncRunOut])
def list_sync_runs(account_id: int, session: Session = Depends(get_session)):
    get_account_or_404(session, account_id)
    return (
        session.execute(
            select(SyncRun)
            .where(SyncRun.account_id == account_id)
            .order_by(SyncRun.started.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
