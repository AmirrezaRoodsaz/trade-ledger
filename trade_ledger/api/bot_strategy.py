"""The strategy view of one bot: the three result columns, the readiness
breakdown, the latest passed backtest and the drill checklist.

Kept out of `api/bots.py` so the registry file stays one concern. The fleet
readiness map lives at the literal `/bots/readiness/all` — `/bots/{slug}`
would otherwise swallow it.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..bots import readiness as readiness_engine
from ..db import get_session
from ..models import Bot
from ._common import get_bot_or_404
from .analytics import StageGateOut
from .backtests import BacktestOut, to_out
from .schemas import BaseModel, Money

router = APIRouter()


class StageResultOut(BaseModel):
    count: int
    expectancy_r: Money
    profit_factor: Money | None
    win_rate: Money
    max_dd_r: Money
    max_dd_eur: Money
    adherence: Money | None
    gate: StageGateOut


class ResultsOut(BaseModel):
    backtest: BacktestOut | None
    incubation: StageResultOut
    live: StageResultOut


class StageOut(BaseModel):
    key: str
    weight: Money
    progress: Money
    factor: Money
    detail: str


class ReadinessOut(BaseModel):
    percent: Money
    stages: list[StageOut]


class DrillOut(BaseModel):
    key: str
    done: bool
    done_ts: datetime | None
    note: str | None


class DrillIn(BaseModel):
    done: bool
    note: str | None = None


class StrategyOut(BaseModel):
    results: ResultsOut
    readiness: ReadinessOut
    backtest: BacktestOut | None
    drills: list[DrillOut]


def _stage_out(result: dict) -> StageResultOut:
    gate = dict(result["gate"])
    gate["checks"] = [
        {**check, "actual": str(check["actual"]), "required": str(check["required"])}
        for check in gate["checks"]
    ]
    return StageResultOut(**{**result, "gate": gate})


@router.get("/bots/readiness/all", response_model=dict[str, Money])
def fleet_readiness(session: Session = Depends(get_session)):
    """`{slug: percent}` for every bot — one call for the fleet cards."""
    bots = session.execute(select(Bot).order_by(Bot.id)).scalars().all()
    return {bot.slug: readiness_engine.readiness(session, bot)["percent"] for bot in bots}


@router.get("/bots/{slug}/strategy", response_model=StrategyOut)
def get_bot_strategy(slug: str, session: Session = Depends(get_session)):
    bot = get_bot_or_404(session, slug)
    results = readiness_engine.stage_results(session, bot)
    backtest = to_out(results["backtest"]) if results["backtest"] is not None else None
    return StrategyOut(
        results=ResultsOut(
            backtest=backtest,
            incubation=_stage_out(results["incubation"]),
            live=_stage_out(results["live"]),
        ),
        readiness=readiness_engine.readiness(session, bot, results),
        backtest=backtest,
        drills=readiness_engine.drills(session, bot),
    )


@router.put("/bots/{slug}/drills/{key}", response_model=DrillOut)
def put_bot_drill(
    slug: str, key: str, payload: DrillIn, session: Session = Depends(get_session)
):
    bot = get_bot_or_404(session, slug)
    if key not in readiness_engine.DRILL_KEYS:
        raise HTTPException(status_code=422, detail=f"unknown drill: {key}")
    return readiness_engine.set_drill(session, bot, key, payload.done, payload.note)
