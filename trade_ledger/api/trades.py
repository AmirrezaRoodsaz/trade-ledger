from __future__ import annotations

import calendar as calendar_mod
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..bots.auth import bot_auth
from ..db import get_session
from ..engine.mae_mfe import compute_excursions
from ..enums import Direction, Mistake, TradeStatus
from ..journal import (
    ManualFill,
    StatusError,
    cancel_trade,
    close_trade,
    open_trade,
    plan_trade,
    review_trade,
    suggest_fills,
)
from ..models import Bot, Instrument, Trade
from ..prices.service import candles as fetch_candles
from ..prices.service import ensure_prices
from ..settings import get_settings
from ._common import get_account_or_404, get_instrument_or_404, mode_filter, trade_filters
from .schemas import BaseModel, Money, Page, UTCDatetime
from .transactions import TransactionOut

router = APIRouter()

SCREENSHOT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024


class TradeIn(BaseModel):
    account_id: int
    instrument_id: int
    direction: Direction
    playbook_version_id: int | None = None
    planned_entry: Money | None = None
    planned_stop: Money | None = None
    planned_target: Money | None = None
    risk_eur: Money | None = None
    planned_qty: Money | None = None
    emotion_pre: str | None = None
    note_pre: str | None = None
    tags: list[str] = Field(default_factory=list)
    external_ref: str | None = None


class TradeOut(BaseModel):
    id: int
    account_id: int
    instrument_id: int
    direction: Direction
    status: TradeStatus
    playbook_version_id: int | None
    planned_entry: Money | None
    planned_stop: Money | None
    planned_target: Money | None
    risk_eur: Money | None
    planned_qty: Money | None
    opened_ts: datetime | None
    closed_ts: datetime | None
    avg_entry: Money | None
    avg_exit: Money | None
    quantity: Money | None
    fees_eur: Money | None
    result_eur: Money | None
    mae_eur: Money | None
    mfe_eur: Money | None
    r_multiple: Money | None
    adherence: bool | None
    mistake: str | None
    emotion_pre: str | None
    emotion_post: str | None
    note_pre: str | None
    note_post: str | None
    screenshots: list[str]
    tags: list[str]
    external_ref: str | None
    bot_id: int | None

    @field_validator("screenshots", "tags", mode="before")
    @classmethod
    def _decode_json_list(cls, value):
        return json.loads(value) if isinstance(value, str) else value


class ManualFillIn(BaseModel):
    ts: UTCDatetime
    quantity: Money
    price: Money
    fee_eur: Money = Decimal(0)


class FillsIn(BaseModel):
    fill_ids: list[int] = Field(default_factory=list)
    manual: ManualFillIn | None = None


class ReviewIn(BaseModel):
    adherence: bool | None = None
    mistake: Mistake | None = None
    emotion_post: str | None = None
    note_post: str | None = None


class CalendarDay(BaseModel):
    count: int
    result_eur: Money
    r: Money


class ExcursionsOut(BaseModel):
    mae_eur: Money
    mfe_eur: Money
    mae_r: Money | None
    mfe_r: Money | None
    resolution: str


class RecomputeOut(BaseModel):
    updated: int
    skipped: int


class ChartCandle(BaseModel):
    time: str
    open: Money | None
    high: Money | None
    low: Money | None
    close: Money | None


class ChartMarker(BaseModel):
    time: str
    kind: str
    price: Money


class ChartOut(BaseModel):
    candles: list[ChartCandle]
    markers: list[ChartMarker]
    resolution: str


def _guard(fn, *args, **kwargs):
    """Run a `journal` call and translate its two failure modes into the
    status codes the API contract promises: 409 for a lifecycle guard, 422
    for bad input.
    """
    try:
        return fn(*args, **kwargs)
    except StatusError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _require_bot_account(account_id: int, bot: Bot | None) -> None:
    """A bot journals into its own account and no other — on create and on
    edit, since an edit could otherwise move the trade across accounts.
    """
    if bot is not None and account_id != bot.account_id:
        raise HTTPException(status_code=403, detail="account does not belong to this bot")


def _get_trade_or_404(session: Session, trade_id: int, bot: Bot | None = None) -> Trade:
    """The trade, and — when a bot token made the call — proof it is that
    bot's own trade. One guard here rather than one per endpoint.
    """
    trade = session.get(Trade, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="trade not found")
    if bot is not None and trade.bot_id != bot.id:
        raise HTTPException(status_code=403, detail="trade belongs to another bot")
    return trade


def _fill_args(payload: FillsIn) -> dict:
    manual = ManualFill(**payload.manual.model_dump()) if payload.manual else None
    return {"fill_ids": payload.fill_ids, "manual": manual}


@router.get("/trades", response_model=Page[TradeOut])
def list_trades(
    account_id: list[int] | None = Query(None),
    mode: str = "paper",
    status: TradeStatus | None = None,
    playbook_id: int | None = None,
    instrument_id: int | None = None,
    tag: str | None = None,
    date_from: UTCDatetime | None = None,
    date_to: UTCDatetime | None = None,
    bot_id: int | None = None,
    external_ref: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1),
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    if bot is not None:
        # A bot sees its own trades and nothing else. Its account has exactly
        # one mode, so the mode filter could only ever hide them.
        bot_id, mode = bot.id, "all"
    stmt = trade_filters(
        select(Trade),
        mode=mode,
        account_id=account_id,
        playbook_id=playbook_id,
        instrument_id=instrument_id,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
    if status is not None:
        stmt = stmt.where(Trade.status == status)
    if bot_id is not None:
        stmt = stmt.where(Trade.bot_id == bot_id)
    if external_ref is not None:
        # Exact match: the journal id is unique per trade, and a bot looks a
        # ref up to find out whether it already filed this intent.
        stmt = stmt.where(Trade.external_ref == external_ref)

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        session.execute(stmt.order_by(Trade.id).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return Page(items=rows, total=total)


@router.get("/trades/calendar", response_model=dict[str, CalendarDay])
def trades_calendar(
    year: int,
    month: int = Query(..., ge=1, le=12),
    mode: str = "paper",
    session: Session = Depends(get_session),
):
    """Closed trades of one month, bucketed by close date."""
    start = datetime(year, month, 1, tzinfo=UTC)
    last_day = calendar_mod.monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=UTC)

    stmt = mode_filter(select(Trade), mode).where(
        Trade.status == TradeStatus.CLOSED, Trade.closed_ts >= start, Trade.closed_ts <= end
    )
    days: dict[str, CalendarDay] = {}
    for trade in session.execute(stmt).scalars():
        key = trade.closed_ts.date().isoformat()
        day = days.setdefault(key, CalendarDay(count=0, result_eur=Decimal(0), r=Decimal(0)))
        day.count += 1
        day.result_eur += trade.result_eur or Decimal(0)
        day.r += trade.r_multiple or Decimal(0)
    return days


@router.post("/trades", response_model=TradeOut, status_code=201)
def create_trade(
    payload: TradeIn,
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    _require_bot_account(payload.account_id, bot)
    get_account_or_404(session, payload.account_id)
    if session.get(Instrument, payload.instrument_id) is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    data = payload.model_dump()
    if bot is not None:
        data["bot_id"] = bot.id
    return _guard(plan_trade, session, data)


def _excursion_window(trade: Trade) -> tuple[date, date]:
    """The trade's life as a date range: opened day to closed day, or today
    for a still-open trade.
    """
    end = trade.closed_ts.date() if trade.closed_ts is not None else datetime.now(UTC).date()
    return trade.opened_ts.date(), end


@router.post("/trades/excursions/recompute", response_model=RecomputeOut)
def recompute_excursions(session: Session = Depends(get_session)):
    """MAE/MFE for every closed trade that has cached candles. Registered
    ahead of `/trades/{trade_id}` so "excursions" is never captured as an id.
    """
    updated = skipped = 0
    trades = session.execute(select(Trade).where(Trade.status == TradeStatus.CLOSED)).scalars()
    for trade in trades:
        instrument = session.get(Instrument, trade.instrument_id)
        start, end = _excursion_window(trade)
        result = compute_excursions(trade, fetch_candles(session, instrument, start, end))
        if result is None:
            skipped += 1
            continue
        trade.mae_eur, trade.mfe_eur = result
        updated += 1
    session.commit()
    return RecomputeOut(updated=updated, skipped=skipped)


@router.get("/trades/{trade_id}", response_model=TradeOut)
def get_trade(
    trade_id: int,
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    return _get_trade_or_404(session, trade_id, bot)


@router.put("/trades/{trade_id}", response_model=TradeOut)
def update_trade(
    trade_id: int,
    payload: TradeIn,
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    trade = _get_trade_or_404(session, trade_id, bot)
    _require_bot_account(payload.account_id, bot)
    if trade.status != TradeStatus.PLANNED:
        # Account, instrument and direction decide what the linked fills mean;
        # once a trade is open they are history. Grading goes through /review.
        raise HTTPException(status_code=409, detail="only planned trades can be edited")
    get_account_or_404(session, payload.account_id)
    if session.get(Instrument, payload.instrument_id) is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    data = payload.model_dump()
    if data["planned_stop"] is not None and data["planned_stop"] == data["planned_entry"]:
        raise HTTPException(status_code=422, detail="planned_stop must differ from planned_entry")
    data["tags"] = json.dumps(data["tags"])
    # The journal id is server-assigned; an edit that omits it keeps it.
    data["external_ref"] = data["external_ref"] or trade.external_ref
    for field, value in data.items():
        setattr(trade, field, value)
    session.commit()
    session.refresh(trade)
    return trade


@router.delete("/trades/{trade_id}", status_code=204)
def delete_trade(
    trade_id: int,
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    trade = _get_trade_or_404(session, trade_id, bot)
    if trade.status not in (TradeStatus.PLANNED, TradeStatus.CANCELLED):
        raise HTTPException(
            status_code=409, detail="only planned or cancelled trades can be deleted"
        )
    session.delete(trade)
    session.commit()


@router.post("/trades/{trade_id}/open", response_model=TradeOut)
def post_open_trade(
    trade_id: int,
    payload: FillsIn,
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    trade = _get_trade_or_404(session, trade_id, bot)
    return _guard(open_trade, session, trade, **_fill_args(payload))


@router.post("/trades/{trade_id}/close", response_model=TradeOut)
def post_close_trade(
    trade_id: int,
    payload: FillsIn,
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    trade = _get_trade_or_404(session, trade_id, bot)
    return _guard(close_trade, session, trade, **_fill_args(payload))


@router.post("/trades/{trade_id}/review", response_model=TradeOut)
def post_review_trade(
    trade_id: int,
    payload: ReviewIn,
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    trade = _get_trade_or_404(session, trade_id, bot)
    return _guard(review_trade, session, trade, **payload.model_dump())


@router.post("/trades/{trade_id}/cancel", response_model=TradeOut)
def post_cancel_trade(
    trade_id: int,
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    trade = _get_trade_or_404(session, trade_id, bot)
    return _guard(cancel_trade, session, trade)


@router.get("/trades/{trade_id}/suggest-fills", response_model=list[TransactionOut])
def get_suggest_fills(
    trade_id: int,
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    trade = _get_trade_or_404(session, trade_id, bot)
    return suggest_fills(session, trade)


@router.post("/trades/{trade_id}/excursions", response_model=ExcursionsOut)
def post_trade_excursions(
    trade_id: int,
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    trade = _get_trade_or_404(session, trade_id, bot)
    if trade.opened_ts is None:
        raise HTTPException(status_code=409, detail="trade has not been opened")
    instrument = session.get(Instrument, trade.instrument_id)
    start, end = _excursion_window(trade)
    result = compute_excursions(trade, fetch_candles(session, instrument, start, end))
    if result is None:
        raise HTTPException(status_code=404, detail="no cached candles for this trade's dates")
    trade.mae_eur, trade.mfe_eur = result
    session.commit()
    return ExcursionsOut(
        mae_eur=trade.mae_eur,
        mfe_eur=trade.mfe_eur,
        mae_r=trade.mae_r,
        mfe_r=trade.mfe_r,
        resolution="1d",
    )


@router.get("/trades/{trade_id}/chart", response_model=ChartOut)
def get_trade_chart(
    trade_id: int,
    padding_days: int = 20,
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    trade = _get_trade_or_404(session, trade_id, bot)
    if trade.opened_ts is None:
        raise HTTPException(status_code=409, detail="trade has not been opened")
    instrument = get_instrument_or_404(session, trade.instrument_id)
    start, end = _excursion_window(trade)
    padding = timedelta(days=padding_days)
    start, end = start - padding, end + padding

    if instrument.price_source:
        try:
            ensure_prices(session, instrument, start, end)
        except httpx.HTTPError:
            pass  # ponytail: offline/rate-limited fetch — fall back to whatever is cached

    rows = fetch_candles(session, instrument, start, end)
    out_candles = [
        ChartCandle(time=c.date.isoformat(), open=c.open, high=c.high, low=c.low, close=c.close)
        for c in rows
    ]

    markers = []
    opened_day = trade.opened_ts.date().isoformat()
    if trade.avg_entry is not None:
        markers.append(ChartMarker(time=opened_day, kind="entry", price=trade.avg_entry))
    if trade.avg_exit is not None and trade.closed_ts is not None:
        markers.append(
            ChartMarker(time=trade.closed_ts.date().isoformat(), kind="exit", price=trade.avg_exit)
        )
    if trade.planned_stop is not None:
        markers.append(ChartMarker(time=opened_day, kind="stop", price=trade.planned_stop))
    if trade.planned_target is not None:
        markers.append(ChartMarker(time=opened_day, kind="target", price=trade.planned_target))

    return ChartOut(candles=out_candles, markers=markers, resolution="1d")


def _stored_name(filename: str) -> str:
    """Collision-free, traversal-free file name with an allowed suffix."""
    name = Path(filename).name
    if Path(name).suffix.lower() not in SCREENSHOT_SUFFIXES:
        raise HTTPException(
            status_code=422, detail="only .png, .jpg, .jpeg and .webp screenshots are allowed"
        )
    return f"{uuid4().hex[:8]}-{re.sub(r'[^A-Za-z0-9._-]', '_', name)}"


@router.post("/trades/{trade_id}/screenshots", response_model=TradeOut)
async def upload_screenshots(
    trade_id: int,
    files: Annotated[list[UploadFile], File()],
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    trade = _get_trade_or_404(session, trade_id, bot)
    data_dir = Path(get_settings().DATA_DIR)
    stored = json.loads(trade.screenshots)
    for upload in files:
        relative = Path("screenshots") / str(trade_id) / _stored_name(upload.filename or "")
        blob = await upload.read()
        if len(blob) > MAX_SCREENSHOT_BYTES:
            raise HTTPException(status_code=413, detail="screenshot larger than 10 MB")
        target = data_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        stored.append(relative.as_posix())

    trade.screenshots = json.dumps(stored)
    session.commit()
    session.refresh(trade)
    return trade
