"""Bot registry: CRUD, token rotation and env-file presence.

The bearer token is returned exactly twice in a bot's life — on create and
on rotate. `BotOut` never carries `token_hash`.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..bots.auth import issue_token
from ..db import get_session
from ..enums import BotHost
from ..models import Bot, BotRun, Trade
from ..settings import _parse_env_file, get_settings
from ._common import get_account_or_404, get_bot_or_404
from .schemas import BaseModel, Money

router = APIRouter()

# Keys the operator writes into `DATA_DIR/bots/<slug>/.env`. Reported as
# presence booleans only — the app must never hand out a bot's exchange
# credentials, and it never reads them for itself.
BOT_ENV_KEYS = (
    "TRADE_LEDGER_URL",
    "BOT_TOKEN",
    "EXCHANGE_ID",
    "EXCHANGE_KEY",
    "EXCHANGE_SECRET",
    "EXCHANGE_PASSPHRASE",
)

_SCHEDULE_AT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def slugify(name: str) -> str:
    """`"OKX Donchian 4h"` -> `"okx-donchian-4h"` — url- and path-safe."""
    return re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-"))


class BotIn(BaseModel):
    name: str
    account_id: int
    strategy: str
    host: BotHost = BotHost.LOCAL
    schedule_every_s: int = 14400
    schedule_at: str = "00:05"
    grace_s: int = 3300
    preset_version_id: int | None = None
    stage_capital_eur: Money | None = None
    enabled: bool = True
    dry_run: bool = False

    @field_validator("schedule_at")
    @classmethod
    def _hh_mm(cls, value: str) -> str:
        if not _SCHEDULE_AT.match(value):
            raise ValueError("schedule_at must be HH:MM (UTC)")
        return value

    @field_validator("schedule_every_s")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("schedule_every_s must be positive")
        return value


class BotOut(BaseModel):
    id: int
    slug: str
    name: str
    account_id: int
    strategy: str
    preset_version_id: int | None
    host: BotHost
    schedule_every_s: int
    schedule_at: str
    grace_s: int
    enabled: bool
    dry_run: bool
    paused_entries: bool
    stage_capital_eur: Money | None
    code_version: str | None
    last_heartbeat: datetime | None
    last_run_id: int | None
    status: str
    created: datetime


class BotCreated(BaseModel):
    bot: BotOut
    token: str


class TokenOut(BaseModel):
    token: str


@router.get("/bots", response_model=list[BotOut])
def list_bots(session: Session = Depends(get_session)):
    return session.execute(select(Bot).order_by(Bot.id)).scalars().all()


@router.post("/bots", response_model=BotCreated, status_code=201)
def create_bot(payload: BotIn, session: Session = Depends(get_session)):
    get_account_or_404(session, payload.account_id)
    slug = slugify(payload.name)
    if not slug:
        raise HTTPException(status_code=422, detail="name yields an empty slug")
    if session.execute(select(Bot.id).where(Bot.slug == slug)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"slug already taken: {slug}")

    token, token_hash = issue_token()
    bot = Bot(slug=slug, token_hash=token_hash, **payload.model_dump())
    session.add(bot)
    session.commit()
    session.refresh(bot)
    return BotCreated(bot=BotOut.model_validate(bot), token=token)


@router.get("/bots/{slug}", response_model=BotOut)
def get_bot(slug: str, session: Session = Depends(get_session)):
    return get_bot_or_404(session, slug)


@router.put("/bots/{slug}", response_model=BotOut)
def update_bot(slug: str, payload: BotIn, session: Session = Depends(get_session)):
    bot = get_bot_or_404(session, slug)
    get_account_or_404(session, payload.account_id)
    # ponytail: the slug is the bot's identity — its env directory, its run
    # logs and the token its process already holds all hang off it, so a
    # rename changes the display name only.
    for field, value in payload.model_dump().items():
        setattr(bot, field, value)
    session.commit()
    session.refresh(bot)
    return bot


@router.delete("/bots/{slug}", status_code=204)
def delete_bot(slug: str, session: Session = Depends(get_session)):
    bot = get_bot_or_404(session, slug)
    for model, column, what in (
        (BotRun, BotRun.bot_id, "runs"),
        (Trade, Trade.bot_id, "trades"),
    ):
        if session.scalar(select(func.count()).select_from(model).where(column == bot.id)):
            raise HTTPException(status_code=409, detail=f"bot has {what}")
    session.delete(bot)
    session.commit()


@router.post("/bots/{slug}/token", response_model=TokenOut)
def rotate_token(slug: str, session: Session = Depends(get_session)):
    """New token, shown once. The old one stops working immediately."""
    bot = get_bot_or_404(session, slug)
    token, bot.token_hash = issue_token()
    session.commit()
    return TokenOut(token=token)


@router.get("/bots/{slug}/env-status", response_model=dict[str, bool])
def get_bot_env_status(slug: str, session: Session = Depends(get_session)):
    """Which keys the bot's env file holds. Presence only, never values."""
    bot = get_bot_or_404(session, slug)
    values = _parse_env_file(env_path(bot.slug))
    return {key: bool(values.get(key)) for key in BOT_ENV_KEYS}


def env_path(slug: str) -> Path:
    return Path(get_settings().DATA_DIR) / "bots" / slug / ".env"
