"""Portfolio-facing routes. This task adds only the price routes; a later
task extends this file with the rest of `/api/portfolio`.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Instrument, Price
from ..prices.service import candles, ensure_fx, ensure_prices
from ._common import get_instrument_or_404
from .schemas import BaseModel, Money

router = APIRouter()


class PriceOut(BaseModel):
    date: date
    open: Money | None
    high: Money | None
    low: Money | None
    close: Money | None


class PriceIn(BaseModel):
    date: date
    close: Money
    ccy: str


class RefreshIn(BaseModel):
    instrument_id: int | None = None
    start: date
    end: date


class RefreshOut(BaseModel):
    prices_written: int
    fx_written: int


@router.post("/prices/refresh", response_model=RefreshOut)
def refresh_prices(payload: RefreshIn, session: Session = Depends(get_session)):
    if payload.instrument_id is not None:
        instruments = [get_instrument_or_404(session, payload.instrument_id)]
    else:
        instruments = (
            session.execute(select(Instrument).where(Instrument.price_source.isnot(None)))
            .scalars()
            .all()
        )

    prices_written = 0
    fx_ccys: set[str] = set()
    for instrument in instruments:
        prices_written += ensure_prices(session, instrument, payload.start, payload.end)
        if instrument.quote_ccy != "EUR":
            fx_ccys.add(instrument.quote_ccy)

    fx_written = sum(ensure_fx(session, ccy, payload.start, payload.end) for ccy in fx_ccys)
    return RefreshOut(prices_written=prices_written, fx_written=fx_written)


@router.get("/prices/{instrument_id}", response_model=list[PriceOut])
def get_prices(
    instrument_id: int,
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    session: Session = Depends(get_session),
):
    instrument = get_instrument_or_404(session, instrument_id)
    return candles(session, instrument, date_from, date_to)


@router.put("/prices/{instrument_id}", response_model=PriceOut)
def set_price(instrument_id: int, payload: PriceIn, session: Session = Depends(get_session)):
    get_instrument_or_404(session, instrument_id)
    row = session.execute(
        select(Price).where(Price.instrument_id == instrument_id, Price.date == payload.date)
    ).scalar_one_or_none()
    if row is None:
        row = Price(instrument_id=instrument_id, date=payload.date)
        session.add(row)
    row.close = payload.close
    row.ccy = payload.ccy
    row.source = "manual"
    session.commit()
    session.refresh(row)
    return row
