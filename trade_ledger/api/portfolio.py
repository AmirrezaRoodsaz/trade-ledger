"""Portfolio-facing routes: cached prices, plus the read-only portfolio views
(holdings, cash flows, valuation, allocation, dividends, fees, returns).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..engine import portfolio, returns
from ..enums import TxType
from ..models import Instrument, Price
from ..prices.service import PriceMissing, candles, ensure_fx, ensure_prices
from ._common import ModeFilter, get_instrument_or_404, resolve_account_ids
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


# --- portfolio ------------------------------------------------------------


def _account_ids(
    account_id: Annotated[list[int] | None, Query()] = None,
    mode: ModeFilter = "paper",
    session: Session = Depends(get_session),
) -> list[int]:
    return resolve_account_ids(session, account_id, mode)


AccountIds = Annotated[list[int], Depends(_account_ids)]


class InstrumentRef(BaseModel):
    id: int
    symbol: str
    asset_class: str
    name: str | None = None


class HoldingOut(BaseModel):
    instrument: InstrumentRef
    quantity: Money
    avg_cost_eur: Money
    cost_eur: Money
    price_eur: Money | None
    value_eur: Money | None
    unrealised_eur: Money | None
    weight: Money


class CashflowOut(BaseModel):
    date: date
    type: TxType
    amount_eur: Money
    account: str


class ValuePoint(BaseModel):
    date: date
    value_eur: Money


class ValueSeriesOut(BaseModel):
    points: list[ValuePoint]
    missing_dates: list[date]


class AllocationOut(BaseModel):
    key: str
    value_eur: Money
    weight: Money


class DividendsOut(BaseModel):
    total: Money
    withholding: Money
    by_instrument: dict[str, Money]
    by_month: dict[str, Money]
    projected_next_12m: Money


class FeesOut(BaseModel):
    by_account: dict[str, Money]
    total: Money


class ReturnsOut(BaseModel):
    ttwror: Money | None
    xirr: Money | None
    start_value: Money | None
    end_value: Money | None
    net_flows: Money


@router.get("/portfolio/holdings", response_model=list[HoldingOut])
def get_holdings(
    account_ids: AccountIds,
    at: date | None = None,
    session: Session = Depends(get_session),
):
    return portfolio.holdings(session, account_ids, portfolio.end_of_day(at) if at else None)


@router.get("/portfolio/cashflows", response_model=list[CashflowOut])
def get_cashflows(
    account_ids: AccountIds,
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    session: Session = Depends(get_session),
):
    return portfolio.cashflows(session, account_ids, date_from, date_to)


@router.get("/portfolio/value-series", response_model=ValueSeriesOut)
def get_value_series(
    account_ids: AccountIds,
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    step_days: int = Query(1, ge=1),
    session: Session = Depends(get_session),
):
    series = portfolio.value_series(session, account_ids, date_from, date_to, step_days)
    priced = {on for on, _ in series}
    grid = []
    on = date_from
    while on <= date_to:
        grid.append(on)
        on += timedelta(days=step_days)
    return ValueSeriesOut(
        points=[ValuePoint(date=on, value_eur=value) for on, value in series],
        missing_dates=[on for on in grid if on not in priced],
    )


@router.get("/portfolio/allocation", response_model=list[AllocationOut])
def get_allocation(
    account_ids: AccountIds,
    by: Literal["asset_class", "venue", "instrument"] = "asset_class",
    session: Session = Depends(get_session),
):
    return portfolio.allocation(session, account_ids, by)


@router.get("/portfolio/dividends", response_model=DividendsOut)
def get_dividends(
    account_ids: AccountIds,
    year: int = Query(ge=1970),
    session: Session = Depends(get_session),
):
    return portfolio.dividends(session, account_ids, year)


@router.get("/portfolio/fees", response_model=FeesOut)
def get_fees(
    account_ids: AccountIds,
    year: int = Query(ge=1970),
    session: Session = Depends(get_session),
):
    return portfolio.fees(session, account_ids, year)


@router.get("/portfolio/returns", response_model=ReturnsOut)
def get_returns(
    account_ids: AccountIds,
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    session: Session = Depends(get_session),
):
    """Time- and money-weighted return over the period.

    Flows strictly after `from` split the TWR into sub-periods. `portfolio_value`
    is an end-of-day figure, so the flow of that day is subtracted back out to
    get the pre-flow value `ttwror` expects. All four figures are `null` when a
    price the valuation needs is missing.
    """
    flows = portfolio.external_flows(session, account_ids, date_from, date_to)
    flows = [(on, amount) for on, amount in flows if on > date_from]
    net_flows = sum((amount for _, amount in flows), Decimal(0))

    flow_by_date: dict[date, Decimal] = {}
    for on, amount in flows:
        flow_by_date[on] = flow_by_date.get(on, Decimal(0)) + amount

    try:
        values = [
            (on, portfolio.portfolio_value(session, account_ids, on) - flow_by_date.get(on, Decimal(0)))
            for on in sorted({date_from, date_to} | set(flow_by_date))
        ]
        start_value = portfolio.portfolio_value(session, account_ids, date_from)
        end_value = portfolio.portfolio_value(session, account_ids, date_to)
    except PriceMissing:
        return ReturnsOut(
            ttwror=None, xirr=None, start_value=None, end_value=None, net_flows=net_flows
        )

    # Investor's view: capital put in is negative, what comes back is positive.
    money_flows = (
        [(date_from, -start_value)]
        + [(on, -amount) for on, amount in flows]
        + [(date_to, end_value)]
    )
    return ReturnsOut(
        ttwror=returns.ttwror(values, flows),
        xirr=returns.xirr(money_flows),
        start_value=start_value,
        end_value=end_value,
        net_flows=net_flows,
    )
