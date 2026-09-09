"""Bot registry: CRUD, token rotation and env-file presence.

The bearer token is returned exactly twice in a bot's life — on create and
on rotate. `BotOut` never carries `token_hash`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..bots import monitor
from ..bots.auth import issue_token
from ..db import get_session
from ..enums import BotHost, BotStatus, EventKind
from ..models import Bot, BotEvent, BotRun, Trade
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


# --- health and history reads -----------------------------------------------


class KillRuleOut(BaseModel):
    rule: str
    status: str
    value: str | None
    threshold: str | None
    action: str
    detail: str


class HealthStateOut(BaseModel):
    ts: datetime
    equity_eur: Money | None
    peak_equity_eur: Money | None
    drawdown_pct: Money | None
    reconciliation: str
    reconciliation_detail: str | None
    positions: list[dict]
    positions_count: int
    positions_without_stop: int
    config_version: int | None


class HealthRunOut(BaseModel):
    id: int
    started: datetime
    finished: datetime | None
    status: str
    error: str | None


class HealthOut(BaseModel):
    status: BotStatus
    kill_rules: list[KillRuleOut]
    last_heartbeat: datetime | None
    next_run: datetime
    deadline: datetime
    last_run: HealthRunOut | None
    state: HealthStateOut | None
    stage_capital_eur: Money


class BotListItem(BotOut):
    """`BotOut` plus what the fleet page shows without opening a bot."""

    next_run: datetime | None = None
    equity_eur: Money | None = None
    drawdown_pct: Money | None = None
    positions_count: int = 0
    positions_without_stop: int = 0
    kill_summary: dict[str, str] = Field(default_factory=dict)


class RunOut(BaseModel):
    id: int
    bot_id: int
    started: datetime
    finished: datetime | None
    status: str
    summary: dict
    error: str | None
    has_log: bool


class EventOut(BaseModel):
    id: int
    bot_id: int
    ts: datetime
    kind: str
    message: str
    payload: dict


@router.get("/bots", response_model=list[BotListItem])
def list_bots(session: Session = Depends(get_session)):
    """The fleet with a freshly derived status and the kill-rule summary.

    ponytail: one health computation per bot rather than a single joined
    query. A fleet is a handful of rows; revisit if that ever stops being
    true.
    """
    items = []
    for bot in session.execute(select(Bot).order_by(Bot.id)).scalars().all():
        report = monitor.health(session, bot)
        state = report["state"] or {}
        item = BotListItem.model_validate(bot)
        item.status = report["status"]
        item.next_run = report["next_run"]
        item.equity_eur = state.get("equity_eur")
        item.drawdown_pct = state.get("drawdown_pct")
        item.positions_count = state.get("positions_count", 0)
        item.positions_without_stop = state.get("positions_without_stop", 0)
        item.kill_summary = {rule["rule"]: rule["status"] for rule in report["kill_rules"]}
        items.append(item)
    return items


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


@router.get("/bots/{slug}/health", response_model=HealthOut)
def get_bot_health(slug: str, session: Session = Depends(get_session)):
    """The app's own verdict, computed fresh. A read: it raises no alert and
    queues no command, so polling this from the UI changes nothing.
    """
    return monitor.health(session, get_bot_or_404(session, slug))


@router.get("/bots/{slug}/runs", response_model=list[RunOut])
def list_bot_runs(
    slug: str,
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
):
    """Newest first."""
    bot = get_bot_or_404(session, slug)
    runs = (
        session.execute(
            select(BotRun).where(BotRun.bot_id == bot.id).order_by(BotRun.id.desc()).limit(limit)
        )
        .scalars()
        .all()
    )
    return [
        RunOut(
            id=run.id,
            bot_id=run.bot_id,
            started=run.started,
            finished=run.finished,
            status=run.status,
            summary=json.loads(run.summary_json or "{}"),
            error=run.error,
            has_log=bool(run.log_path),
        )
        for run in runs
    ]


@router.get("/bots/{slug}/runs/{run_id}/log", response_class=PlainTextResponse)
def get_bot_run_log(slug: str, run_id: int, session: Session = Depends(get_session)):
    """The captured stdout of one run, as text."""
    bot = get_bot_or_404(session, slug)
    run = session.get(BotRun, run_id)
    if run is None or run.bot_id != bot.id:
        raise HTTPException(status_code=404, detail="run not found")
    # ponytail: the stored path is trusted because only `bot_push.finish_run`
    # writes it, and it builds the path from DATA_DIR and the run id.
    if not run.log_path or not Path(run.log_path).is_file():
        raise HTTPException(status_code=404, detail="no log for this run")
    return PlainTextResponse(Path(run.log_path).read_text())


@router.get("/bots/{slug}/events", response_model=list[EventOut])
def list_bot_events(
    slug: str,
    kind: EventKind | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_session),
):
    """Newest first, optionally one kind only."""
    bot = get_bot_or_404(session, slug)
    stmt = (
        select(BotEvent).where(BotEvent.bot_id == bot.id).order_by(BotEvent.id.desc()).limit(limit)
    )
    if kind is not None:
        stmt = stmt.where(BotEvent.kind == kind)
    return [
        EventOut(
            id=event.id,
            bot_id=event.bot_id,
            ts=event.ts,
            kind=event.kind,
            message=event.message,
            payload=json.loads(event.payload_json or "{}"),
        )
        for event in session.execute(stmt).scalars().all()
    ]
