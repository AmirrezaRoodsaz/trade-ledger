from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_session
from ..enums import AssetClass, FundType, TaxRegime
from ..models import Instrument
from .schemas import BaseModel

router = APIRouter()


class InstrumentIn(BaseModel):
    symbol: str
    asset_class: AssetClass
    isin: str | None = None
    quote_ccy: str = "EUR"
    name: str | None = None
    price_source: str | None = None
    price_symbol: str | None = None
    fund_type: FundType | None = None
    tax_regime: TaxRegime | None = None


class InstrumentOut(InstrumentIn):
    id: int


@router.get("/instruments", response_model=list[InstrumentOut])
def list_instruments(session: Session = Depends(get_session)):
    return session.execute(select(Instrument)).scalars().all()


@router.post("/instruments", response_model=InstrumentOut, status_code=201)
def create_instrument(payload: InstrumentIn, session: Session = Depends(get_session)):
    instrument = Instrument(**payload.model_dump())
    session.add(instrument)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=422, detail="instrument with this symbol and asset_class already exists"
        ) from exc
    session.refresh(instrument)
    return instrument


@router.put("/instruments/{instrument_id}", response_model=InstrumentOut)
def update_instrument(
    instrument_id: int, payload: InstrumentIn, session: Session = Depends(get_session)
):
    instrument = session.get(Instrument, instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    for field, value in payload.model_dump().items():
        setattr(instrument, field, value)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=422, detail="instrument with this symbol and asset_class already exists"
        ) from exc
    session.refresh(instrument)
    return instrument
