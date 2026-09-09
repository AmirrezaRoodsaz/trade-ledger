"""Push protocol: what a bot process is allowed to say to the app.

Every route needs a bot token *and* the token's bot must be the `{slug}` in
the path — a token is a key to one bot, not to the fleet. Unlike the rest of
the API, a missing `Authorization` header is a 401 here rather than local-UI
behaviour: these endpoints only ever have a bot on the other end.

The app never calls an exchange; it only records what the bot pushed and
hands back config and pending commands.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..bots import commands
from ..bots.auth import bot_auth
from ..db import get_session
from ..enums import EventKind, Mode, RunStatus
from ..models import Bot, BotCommand, BotEvent, BotRun, BotState, Preset, PresetVersion, Setting
from ..settings import get_settings
from ._common import get_account_or_404
from .schemas import BaseModel, Money, UTCDatetime

router = APIRouter()

# One call carries one run's worth of events. Above this a bot is looping,
# and the app should say so rather than swallow the flood.
MAX_EVENTS = 500

STAGE_CAPITAL_DEFAULTS = {
    Mode.PAPER: Decimal(2500),
    Mode.DEMO: Decimal(2500),
    Mode.LIVE: Decimal(250),
}


def bot_for_slug(slug: str, bot: Bot | None = Depends(bot_auth)) -> Bot:
    """The authenticated bot, which must be the one named in the path."""
    if bot is None:
        raise HTTPException(status_code=401, detail="bot token required")
    if bot.slug != slug:
        raise HTTPException(status_code=403, detail="token belongs to another bot")
    return bot


def _now() -> datetime:
    return datetime.now(UTC)


def _event(
    session: Session, bot: Bot, kind: EventKind, message: str = "", payload: dict | None = None
) -> None:
    session.add(
        BotEvent(
            bot_id=bot.id,
            ts=_now(),
            kind=kind,
            message=message,
            payload_json=json.dumps(payload or {}),
        )
    )


def _run_or_404(session: Session, bot: Bot, run_id: int) -> BotRun:
    run = session.get(BotRun, run_id)
    if run is None or run.bot_id != bot.id:
        raise HTTPException(status_code=404, detail="run not found")
    return run


def log_path(slug: str, run_id: int) -> Path:
    return Path(get_settings().DATA_DIR) / "bots" / slug / "runs" / f"{run_id}.log"


class HeartbeatIn(BaseModel):
    ts: UTCDatetime | None = None
    host: str | None = None
    code_version: str | None = None
    next_run: UTCDatetime | None = None


class RunStartIn(BaseModel):
    started: UTCDatetime | None = None


class RunStarted(BaseModel):
    id: int


class RunFinishIn(BaseModel):
    status: RunStatus
    summary: dict = Field(default_factory=dict)
    error: str | None = None
    log: str | None = None


class StateIn(BaseModel):
    ts: UTCDatetime | None = None
    equity_eur: Money | None = None
    # ponytail: positions and orders are passed through as JSON. Only
    # `stop_present` is ever read app-side (kill rule K4); modelling the rest
    # would just duplicate the exchange's shape.
    positions: list[dict] = Field(default_factory=list)
    open_orders: list[dict] = Field(default_factory=list)
    reconciliation: Literal["ok", "mismatch", "unknown"] = "unknown"
    reconciliation_detail: str | None = None
    config_version: int | None = None
    extra: dict = Field(default_factory=dict)


class StateOut(BaseModel):
    ts: datetime
    equity_eur: Money | None
    peak_equity_eur: Money | None


class EventIn(BaseModel):
    ts: UTCDatetime | None = None
    kind: EventKind
    message: str = ""
    payload: dict = Field(default_factory=dict)


class EventsOut(BaseModel):
    inserted: int


class ConfigBot(BaseModel):
    slug: str
    account_id: int
    dry_run: bool
    paused_entries: bool
    enabled: bool
    mode: str
    venue: str
    stage_capital_eur: Money


class ConfigPreset(BaseModel):
    version_id: int
    version: int
    strategy: str
    params: dict
    timeframe: str
    pairs: list[str]
    risk_pct: Money | None
    max_position_pct: Money | None
    leverage_cap: Money


class ConfigCommand(BaseModel):
    id: int
    kind: str
    reason: str | None
    issued_ts: datetime


class ConfigOut(BaseModel):
    bot: ConfigBot
    preset: ConfigPreset | None
    commands: list[ConfigCommand]


class AckIn(BaseModel):
    result: str
    detail: str | None = None


class Ok(BaseModel):
    ok: bool = True


@router.post("/bots/{slug}/heartbeat", response_model=Ok)
def heartbeat(
    payload: HeartbeatIn,
    bot: Bot = Depends(bot_for_slug),
    session: Session = Depends(get_session),
):
    """Still alive, and here is when the bot plans to run next.

    `host` is accepted because bots send it, but the registry owns that
    field — a bot cannot move itself between local and remote.
    """
    bot.last_heartbeat = payload.ts or _now()
    if payload.code_version is not None:
        bot.code_version = payload.code_version
    next_run = payload.next_run.isoformat() if payload.next_run else None
    _event(session, bot, EventKind.HEARTBEAT, payload={"next_run": next_run})
    session.commit()
    return Ok()


@router.post("/bots/{slug}/runs", response_model=RunStarted, status_code=201)
def start_run(
    payload: RunStartIn,
    bot: Bot = Depends(bot_for_slug),
    session: Session = Depends(get_session),
):
    run = BotRun(bot_id=bot.id, started=payload.started or _now(), status=RunStatus.RUNNING)
    session.add(run)
    session.flush()
    bot.last_run_id = run.id
    session.commit()
    return RunStarted(id=run.id)


@router.patch("/bots/{slug}/runs/{run_id}", response_model=Ok)
def finish_run(
    run_id: int,
    payload: RunFinishIn,
    bot: Bot = Depends(bot_for_slug),
    session: Session = Depends(get_session),
):
    run = _run_or_404(session, bot, run_id)
    run.status = payload.status
    run.summary_json = json.dumps(payload.summary)
    run.error = payload.error
    run.finished = _now()
    if payload.log is not None:
        target = log_path(bot.slug, run.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload.log)
        run.log_path = str(target)
    if payload.status == RunStatus.ERROR:
        _event(
            session,
            bot,
            EventKind.ERROR,
            message=payload.error or "run failed",
            payload={"run_id": run.id},
        )
    session.commit()
    return Ok()


@router.post("/bots/{slug}/state", response_model=StateOut)
def push_state(
    payload: StateIn,
    bot: Bot = Depends(bot_for_slug),
    session: Session = Depends(get_session),
):
    """One row per bot, overwritten. The peak equity is the exception: it
    only ever grows, because the drawdown kill rule measures against it.
    """
    state = session.execute(select(BotState).where(BotState.bot_id == bot.id)).scalar_one_or_none()
    if state is None:
        state = BotState(bot_id=bot.id)
        session.add(state)
        try:
            session.flush()
        except IntegrityError:
            # ponytail: two pushes for the same bot raced. The unique index
            # is the arbiter — take the row that won and update that.
            session.rollback()
            state = session.execute(
                select(BotState).where(BotState.bot_id == bot.id)
            ).scalar_one()

    state.ts = payload.ts or _now()
    state.equity_eur = payload.equity_eur
    if payload.equity_eur is not None:
        state.peak_equity_eur = max(state.peak_equity_eur or Decimal(0), payload.equity_eur)
    state.positions_json = json.dumps(payload.positions)
    state.open_orders_json = json.dumps(payload.open_orders)
    state.reconciliation = payload.reconciliation
    state.reconciliation_detail = payload.reconciliation_detail
    state.config_version = payload.config_version
    state.extra_json = json.dumps(payload.extra)

    if payload.reconciliation == "mismatch":
        _event(
            session,
            bot,
            EventKind.RECONCILE,
            message=payload.reconciliation_detail or "reconciliation mismatch",
            payload={"reconciliation": payload.reconciliation},
        )
    session.commit()
    return StateOut.model_validate(state)


@router.post("/bots/{slug}/events", response_model=EventsOut, status_code=201)
def push_events(
    payload: list[EventIn],
    bot: Bot = Depends(bot_for_slug),
    session: Session = Depends(get_session),
):
    if len(payload) > MAX_EVENTS:
        raise HTTPException(status_code=413, detail=f"at most {MAX_EVENTS} events per call")
    now = _now()
    session.add_all(
        BotEvent(
            bot_id=bot.id,
            ts=one.ts or now,
            kind=one.kind,
            message=one.message,
            payload_json=json.dumps(one.payload),
        )
        for one in payload
    )
    session.commit()
    return EventsOut(inserted=len(payload))


def stage_capital_eur(session: Session, bot: Bot, mode: str) -> Decimal:
    """Bot override, else the per-mode Setting, else the ladder's default."""
    if bot.stage_capital_eur is not None:
        return bot.stage_capital_eur
    setting = session.get(Setting, f"stage_capital_{mode}")
    if setting is not None:
        try:
            return Decimal(setting.value)
        except InvalidOperation as exc:
            raise HTTPException(
                status_code=500, detail=f"invalid stage_capital setting: {setting.key}"
            ) from exc
    return STAGE_CAPITAL_DEFAULTS[mode]


@router.get("/bots/{slug}/config", response_model=ConfigOut)
def get_config(
    bot: Bot = Depends(bot_for_slug),
    session: Session = Depends(get_session),
):
    """Everything the bot needs for one run: flags, preset and the commands
    it has not acknowledged yet, oldest first.
    """
    account = get_account_or_404(session, bot.account_id)
    preset = None
    if bot.preset_version_id is not None:
        version = session.get(PresetVersion, bot.preset_version_id)
        if version is not None:
            preset = ConfigPreset(
                version_id=version.id,
                version=version.version,
                strategy=session.get(Preset, version.preset_id).strategy,
                params=json.loads(version.params_json),
                timeframe=version.timeframe,
                pairs=json.loads(version.pairs_json),
                risk_pct=version.risk_pct,
                max_position_pct=version.max_position_pct,
                leverage_cap=version.leverage_cap,
            )

    pending = (
        session.execute(
            select(BotCommand)
            .where(BotCommand.bot_id == bot.id, BotCommand.acked_ts.is_(None))
            .order_by(BotCommand.issued_ts, BotCommand.id)
        )
        .scalars()
        .all()
    )
    return ConfigOut(
        bot=ConfigBot(
            slug=bot.slug,
            account_id=bot.account_id,
            dry_run=bot.dry_run,
            paused_entries=bot.paused_entries,
            enabled=bot.enabled,
            mode=account.mode,
            venue=account.venue,
            stage_capital_eur=stage_capital_eur(session, bot, account.mode),
        ),
        preset=preset,
        commands=[ConfigCommand.model_validate(one) for one in pending],
    )


@router.post("/bots/{slug}/commands/{command_id}/ack", response_model=Ok)
def ack_command(
    command_id: int,
    payload: AckIn,
    bot: Bot = Depends(bot_for_slug),
    session: Session = Depends(get_session),
):
    """The bot confirms it carried a command out. `commands.ack` holds the
    flag semantics — a failed pause must stay pending in the operator's
    view, and the UI's own command route shares this implementation.
    """
    command = session.get(BotCommand, command_id)
    if command is None or command.bot_id != bot.id:
        raise HTTPException(status_code=404, detail="command not found")

    commands.ack(session, command, payload.result, payload.detail)
    return Ok()
