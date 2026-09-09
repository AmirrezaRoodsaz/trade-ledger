"""Alerts the monitor raised: read them, tick them off."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Alert
from ._common import get_bot_or_404
from .schemas import BaseModel

router = APIRouter()


class AlertOut(BaseModel):
    id: int
    bot_id: int | None
    ts: datetime
    severity: str
    kind: str
    message: str
    sent_telegram: bool
    acknowledged: bool


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    bot: str | None = None,
    unacked: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_session),
):
    """Newest first. `bot` is a slug; `unacked=true` hides what was ticked off."""
    stmt = select(Alert).order_by(Alert.ts.desc(), Alert.id.desc()).limit(limit)
    if bot is not None:
        stmt = stmt.where(Alert.bot_id == get_bot_or_404(session, bot).id)
    if unacked:
        stmt = stmt.where(Alert.acknowledged.is_(False))
    return session.execute(stmt).scalars().all()


@router.post("/alerts/{alert_id}/ack", response_model=AlertOut)
def ack_alert(alert_id: int, session: Session = Depends(get_session)):
    """Seen it. The 6-hour cooldown is unaffected — acknowledging is not
    "tell me again", it is "I read it".
    """
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    alert.acknowledged = True
    session.commit()
    return alert
