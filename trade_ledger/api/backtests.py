"""Backtest results: list, create (UI JSON body or bot push), upload a JSON
file, delete. The app never backtests — `bots.backtests.evaluate` only judges
the numbers it is handed and stores the verdict with the row.
"""

from __future__ import annotations

import json
from datetime import date as date_
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..bots.auth import bot_auth
from ..bots.backtests import criteria, evaluate
from ..db import get_session
from ..models import BacktestResult, Bot
from .schemas import BaseModel, Money

router = APIRouter()

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class BacktestIn(BaseModel):
    # Optional only for a bot push, where the bot's own strategy is stamped.
    strategy: str | None = None
    preset_version_id: int | None = None
    label: str = ""
    period_start: date_
    period_end: date_
    data_source: str = ""
    timeframe: str = "4h"
    pairs: list[str] = Field(default_factory=list)
    costs_note: str = ""
    trades: int = 0
    expectancy_r: Money = Decimal(0)
    profit_factor: Money | None = None
    win_rate: Money = Decimal(0)
    max_drawdown_pct: Money = Decimal(0)
    cagr_pct: Money | None = None
    benchmark_cagr_pct: Money | None = None
    benchmark_max_drawdown_pct: Money | None = None
    equity_r: list[list] | None = None
    notes: str = ""


class BacktestOut(BaseModel):
    id: int
    strategy: str
    preset_version_id: int | None
    bot_id: int | None
    label: str
    period_start: date_
    period_end: date_
    data_source: str
    timeframe: str
    pairs: list[str]
    costs_note: str
    trades: int
    expectancy_r: Money
    profit_factor: Money | None
    win_rate: Money
    max_drawdown_pct: Money
    cagr_pct: Money | None
    benchmark_cagr_pct: Money | None
    benchmark_max_drawdown_pct: Money | None
    equity_r: list | None
    notes: str
    passed: bool
    fail_reasons: list[str]
    created: datetime


def to_out(row: BacktestResult) -> BacktestOut:
    return BacktestOut(
        pairs=json.loads(row.pairs_json or "[]"),
        equity_r=json.loads(row.equity_json) if row.equity_json else None,
        fail_reasons=json.loads(row.fail_reasons_json or "[]"),
        **{
            field: getattr(row, field)
            for field in BacktestOut.model_fields
            if field not in ("pairs", "equity_r", "fail_reasons")
        },
    )


def _store(session: Session, payload: BacktestIn, bot: Bot | None) -> BacktestResult:
    """A bot files results for its own strategy and nothing else — otherwise
    one bot could raise another bot's readiness through the strategy-wide
    fallback in `bots.readiness`.
    """
    values = payload.model_dump()
    if bot is None:
        if not values["strategy"]:
            raise HTTPException(status_code=422, detail="strategy is required")
    else:
        if values["strategy"] and values["strategy"] != bot.strategy:
            raise HTTPException(
                status_code=422,
                detail=f"bot {bot.slug} may only file results for {bot.strategy}",
            )
        values["strategy"] = bot.strategy
        if values["preset_version_id"] is None:
            values["preset_version_id"] = bot.preset_version_id
    pairs = values.pop("pairs")
    equity = values.pop("equity_r")
    passed, reasons = evaluate(values, criteria(session))
    row = BacktestResult(
        **values,
        bot_id=bot.id if bot is not None else None,
        pairs_json=json.dumps(pairs),
        equity_json=json.dumps(equity, default=str) if equity else None,
        passed=passed,
        fail_reasons_json=json.dumps(reasons),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.get("/backtests", response_model=list[BacktestOut])
def list_backtests(
    strategy: str | None = None,
    bot: str | None = None,
    session: Session = Depends(get_session),
):
    stmt = select(BacktestResult).order_by(BacktestResult.created.desc(), BacktestResult.id.desc())
    if strategy is not None:
        stmt = stmt.where(BacktestResult.strategy == strategy)
    if bot is not None:
        stmt = stmt.where(BacktestResult.bot_id.in_(select(Bot.id).where(Bot.slug == bot)))
    return [to_out(row) for row in session.execute(stmt).scalars()]


@router.post("/backtests", response_model=BacktestOut, status_code=201)
def create_backtest(
    payload: BacktestIn,
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    """A bot's own push stamps `bot_id`; the local UI leaves it unset."""
    return to_out(_store(session, payload, bot))


@router.post("/backtests/upload", response_model=BacktestOut, status_code=201)
async def upload_backtest(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    bot: Bot | None = Depends(bot_auth),
):
    if (file.size or 0) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file larger than 5 MB")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file larger than 5 MB")
    try:
        payload = BacktestIn.model_validate(json.loads(data))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"not valid JSON: {exc}") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
    return to_out(_store(session, payload, bot))


@router.delete("/backtests/{result_id}", status_code=204)
def delete_backtest(result_id: int, session: Session = Depends(get_session)):
    row = session.get(BacktestResult, result_id)
    if row is None:
        raise HTTPException(status_code=404, detail="backtest result not found")
    session.delete(row)
    session.commit()
