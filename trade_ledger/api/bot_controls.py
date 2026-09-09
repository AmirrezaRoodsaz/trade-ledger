"""Bot controls: issuing commands and assigning presets from the UI (or
Telegram/system callers, later). Kept out of `api/bots.py` (the registry
file) and out of `api/bot_push.py` (the bot's own voice, authenticated with
its token) — these routes are the operator's, unauthenticated like the rest
of the local UI.

Every path here is a literal sub-path of `/bots/{slug}` (`/commands`,
`/preset`) so it never collides with a route `api/bots.py` declares on
`/bots/{slug}` itself.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..bots import commands, presets
from ..bots.commands import CommandError
from ..bots.presets import PresetError
from ..db import get_session
from ..enums import CommandKind
from ..models import BotCommand, BotEvent, PresetVersion
from ._common import get_bot_or_404
from .schemas import BaseModel

router = APIRouter()


class CommandIn(BaseModel):
    kind: CommandKind
    reason: str | None = None
    confirm: str | None = None


class CommandOut(BaseModel):
    id: int
    bot_id: int
    kind: str
    reason: str | None
    issued_ts: datetime
    issued_by: str
    acked_ts: datetime | None
    result: str | None
    result_detail: str | None


class PresetAssignIn(BaseModel):
    version_id: int
    reason: str | None = None


class PresetAssignOut(BaseModel):
    preset_version_id: int
    event_id: int


@router.post("/bots/{slug}/commands", response_model=CommandOut, status_code=201)
def create_command(slug: str, payload: CommandIn, session: Session = Depends(get_session)):
    bot = get_bot_or_404(session, slug)
    try:
        command = commands.issue(
            session,
            bot,
            payload.kind,
            reason=payload.reason,
            issued_by="ui",
            confirm=payload.confirm,
        )
    except CommandError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return command


@router.get("/bots/{slug}/commands", response_model=list[CommandOut])
def list_commands(slug: str, limit: int = 50, session: Session = Depends(get_session)):
    bot = get_bot_or_404(session, slug)
    rows = (
        session.execute(
            select(BotCommand)
            .where(BotCommand.bot_id == bot.id)
            .order_by(BotCommand.issued_ts.desc(), BotCommand.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return rows


@router.post("/bots/{slug}/preset", response_model=PresetAssignOut)
def assign_preset(slug: str, payload: PresetAssignIn, session: Session = Depends(get_session)):
    bot = get_bot_or_404(session, slug)
    version = session.get(PresetVersion, payload.version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="preset version not found")
    try:
        event: BotEvent = presets.assign(session, bot, version, payload.reason)
    except PresetError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return PresetAssignOut(preset_version_id=version.id, event_id=event.id)
