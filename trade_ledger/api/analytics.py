"""Trade performance analytics: stats, breakdowns, equity curve, R
histogram, monthly calendar and the stage-gate check. All routes share the
`/trades` filter set (account, mode, playbook, instrument, tag, date range)
via `_common.trade_filters`; the actual numbers come from the pure
`engine.analytics` module — this file only fetches rows and shapes them.
"""

from __future__ import annotations

from datetime import date as date_
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..engine import analytics
from ..models import Account, Instrument, Playbook, PlaybookVersion, Setting, Trade
from ._common import trade_filters
from .schemas import BaseModel, Money, UTCDatetime

router = APIRouter(prefix="/analytics")

_BREAKDOWN_KEYS = (
    "playbook",
    "instrument",
    "weekday",
    "month",
    "hold_bucket",
    "tag",
    "emotion",
    "account",
)


class StatsOut(BaseModel):
    count: int
    wins: int
    losses: int
    flat: int
    win_rate: Money
    expectancy_r: Money
    total_r: Money
    total_eur: Money
    avg_win_r: Money
    avg_loss_r: Money
    payoff: Money | None
    profit_factor: Money | None
    sqn: Money | None
    std_r: Money | None
    max_dd_r: Money
    max_dd_eur: Money
    recovery_factor: Money | None
    max_win_streak: int
    max_loss_streak: int
    avg_hold_hours_win: Money | None
    avg_hold_hours_loss: Money | None
    adherence: Money | None
    cost_of_mistakes_r: Money
    cost_of_mistakes_eur: Money
    mistakes: dict[str, int]
    avg_mae_r: Money | None
    avg_mfe_r: Money | None
    capture: Money | None


class EquityPoint(BaseModel):
    date: date_
    cum_r: Money
    cum_eur: Money
    dd_r: Money
    dd_eur: Money


class HistogramBin(BaseModel):
    lo: Money
    hi: Money
    count: int


class CalendarEntry(BaseModel):
    count: int
    r: Money
    eur: Money


class StageCheck(BaseModel):
    name: str
    passed: bool
    actual: str
    required: str


class StageGateOut(BaseModel):
    mode: str
    capital: Money
    checks: list[StageCheck]
    passed: bool
    next: str


def _filtered_trades(
    session: Session,
    *,
    account_id: list[int] | None,
    mode: str,
    playbook_id: int | None,
    instrument_id: int | None,
    tag: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[Trade]:
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
    return list(session.execute(stmt).scalars().all())


def _enrich_for_breakdown(session: Session, trades: list[Trade], by: str) -> None:
    """Attach the name attributes `engine.analytics.breakdown` reads for the
    `playbook`/`instrument`/`account` keys — the engine is pure and never
    queries the DB itself, so a Trade's foreign-key ids are resolved here.
    """
    if by == "instrument":
        ids = {t.instrument_id for t in trades}
        symbols = dict(
            session.execute(select(Instrument.id, Instrument.symbol).where(Instrument.id.in_(ids))).all()
        )
        for t in trades:
            t.instrument_symbol = symbols.get(t.instrument_id)
    elif by == "account":
        ids = {t.account_id for t in trades}
        names = dict(
            session.execute(select(Account.id, Account.name).where(Account.id.in_(ids))).all()
        )
        for t in trades:
            t.account_name = names.get(t.account_id)
    elif by == "playbook":
        ids = {t.playbook_version_id for t in trades if t.playbook_version_id is not None}
        labels: dict[int, str] = {}
        if ids:
            rows = session.execute(
                select(PlaybookVersion.id, PlaybookVersion.version, Playbook.name)
                .join(Playbook, Playbook.id == PlaybookVersion.playbook_id)
                .where(PlaybookVersion.id.in_(ids))
            ).all()
            labels = {pv_id: f"{name} v{version}" for pv_id, version, name in rows}
        for t in trades:
            t.playbook_label = labels.get(t.playbook_version_id, "none")


def _capital_setting(session: Session, mode: str) -> Decimal:
    key = f"stage_capital_{mode}"
    default = "2500" if mode == "paper" else "250"
    row = session.get(Setting, key)
    return Decimal(row.value) if row is not None else Decimal(default)


@router.get("/stats", response_model=StatsOut)
def get_stats(
    account_id: list[int] | None = Query(None),
    mode: str = "paper",
    playbook_id: int | None = None,
    instrument_id: int | None = None,
    tag: str | None = None,
    date_from: UTCDatetime | None = None,
    date_to: UTCDatetime | None = None,
    session: Session = Depends(get_session),
):
    trades = _filtered_trades(
        session,
        account_id=account_id,
        mode=mode,
        playbook_id=playbook_id,
        instrument_id=instrument_id,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
    return analytics.compute_stats(trades)


@router.get("/breakdown", response_model=dict[str, StatsOut])
def get_breakdown(
    by: str,
    account_id: list[int] | None = Query(None),
    mode: str = "paper",
    playbook_id: int | None = None,
    instrument_id: int | None = None,
    tag: str | None = None,
    date_from: UTCDatetime | None = None,
    date_to: UTCDatetime | None = None,
    session: Session = Depends(get_session),
):
    if by not in _BREAKDOWN_KEYS:
        raise HTTPException(status_code=422, detail=f"unknown breakdown key: {by}")
    trades = _filtered_trades(
        session,
        account_id=account_id,
        mode=mode,
        playbook_id=playbook_id,
        instrument_id=instrument_id,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
    _enrich_for_breakdown(session, trades, by)
    return analytics.breakdown(trades, by)


@router.get("/equity", response_model=list[EquityPoint])
def get_equity(
    account_id: list[int] | None = Query(None),
    mode: str = "paper",
    playbook_id: int | None = None,
    instrument_id: int | None = None,
    tag: str | None = None,
    date_from: UTCDatetime | None = None,
    date_to: UTCDatetime | None = None,
    session: Session = Depends(get_session),
):
    trades = _filtered_trades(
        session,
        account_id=account_id,
        mode=mode,
        playbook_id=playbook_id,
        instrument_id=instrument_id,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
    return analytics.equity_curve(trades)


@router.get("/histogram", response_model=list[HistogramBin])
def get_histogram(
    account_id: list[int] | None = Query(None),
    mode: str = "paper",
    playbook_id: int | None = None,
    instrument_id: int | None = None,
    tag: str | None = None,
    date_from: UTCDatetime | None = None,
    date_to: UTCDatetime | None = None,
    bin: Money = Decimal("0.5"),
    session: Session = Depends(get_session),
):
    trades = _filtered_trades(
        session,
        account_id=account_id,
        mode=mode,
        playbook_id=playbook_id,
        instrument_id=instrument_id,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
    return analytics.r_histogram(trades, bin)


@router.get("/calendar", response_model=dict[str, CalendarEntry])
def get_calendar(
    year: int,
    month: int = Query(..., ge=1, le=12),
    account_id: list[int] | None = Query(None),
    mode: str = "paper",
    playbook_id: int | None = None,
    instrument_id: int | None = None,
    tag: str | None = None,
    date_from: UTCDatetime | None = None,
    date_to: UTCDatetime | None = None,
    session: Session = Depends(get_session),
):
    trades = _filtered_trades(
        session,
        account_id=account_id,
        mode=mode,
        playbook_id=playbook_id,
        instrument_id=instrument_id,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
    return analytics.calendar(trades, year, month)


@router.get("/stage-gate", response_model=StageGateOut)
def get_stage_gate(
    mode: str = "paper",
    capital: Money | None = None,
    account_id: list[int] | None = Query(None),
    playbook_id: int | None = None,
    instrument_id: int | None = None,
    tag: str | None = None,
    date_from: UTCDatetime | None = None,
    date_to: UTCDatetime | None = None,
    session: Session = Depends(get_session),
):
    if mode not in ("paper", "live"):
        raise HTTPException(status_code=422, detail="mode must be 'paper' or 'live' for the stage gate")
    trades = _filtered_trades(
        session,
        account_id=account_id,
        mode=mode,
        playbook_id=playbook_id,
        instrument_id=instrument_id,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
    if capital is None:
        capital = _capital_setting(session, mode)
    result = analytics.stage_gate(trades, mode, capital)
    result["checks"] = [
        {**c, "actual": str(c["actual"]), "required": str(c["required"])} for c in result["checks"]
    ]
    return result
