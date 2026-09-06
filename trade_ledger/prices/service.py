"""Orchestrates `ecb`/`bitstamp`/`stooq` against the `Price`/`FxRate` cache,
and resolves pending transaction EUR amounts once rates are available.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FxRate, Instrument, Price, Transaction
from . import bitstamp, ecb, stooq

# ponytail: USDT/USDC are USD-pegged stablecoins with no ECB series of their
# own — priced as USD for FX purposes. Add a mapping entry if another
# stablecoin needs the same treatment.
_STABLECOIN_TO_FIAT = {"USDT": "USD", "USDC": "USD"}

_FX_LOOKBACK_DAYS = 7


class PriceMissing(Exception):
    """No FX rate found for the requested currency within the lookback window."""


@dataclass
class Candle:
    date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None


def get_fx(session: Session, ccy: str, on: date) -> Decimal:
    """EUR value of 1 unit of `ccy` on `on`. `EUR` is always `1`. Falls back to
    the latest stored rate up to 7 days back (weekends/holidays have no ECB
    print); raises `PriceMissing` if nothing is found in that window.
    """
    ccy = _STABLECOIN_TO_FIAT.get(ccy, ccy)
    if ccy == "EUR":
        return Decimal(1)
    for offset in range(_FX_LOOKBACK_DAYS + 1):
        rate = session.execute(
            select(FxRate.rate_to_eur).where(
                FxRate.ccy == ccy, FxRate.date == on - timedelta(days=offset)
            )
        ).scalar_one_or_none()
        if rate is not None:
            return rate
    raise PriceMissing(f"no {ccy} rate within {_FX_LOOKBACK_DAYS} days of {on}")


def get_close(session: Session, instrument: Instrument, on: date) -> Decimal | None:
    """Cached close (native `Price.ccy`) for `instrument` on `on`, or `None`."""
    return session.execute(
        select(Price.close).where(Price.instrument_id == instrument.id, Price.date == on)
    ).scalar_one_or_none()


def get_close_eur(session: Session, instrument: Instrument, on: date) -> Decimal | None:
    """`get_close` converted to EUR via `get_fx(instrument.quote_ccy, on)`."""
    close = get_close(session, instrument, on)
    if close is None:
        return None
    return close * get_fx(session, instrument.quote_ccy, on)


def _upsert_price(
    session: Session,
    instrument_id: int,
    on: date,
    o: Decimal,
    h: Decimal,
    low: Decimal,
    c: Decimal,
    ccy: str,
    source: str,
) -> None:
    row = session.execute(
        select(Price).where(Price.instrument_id == instrument_id, Price.date == on)
    ).scalar_one_or_none()
    if row is None:
        row = Price(instrument_id=instrument_id, date=on)
        session.add(row)
    row.open, row.high, row.low, row.close = o, h, low, c
    row.ccy = ccy
    row.source = source


def ensure_prices(
    session: Session,
    instrument: Instrument,
    start: date,
    end: date,
    client: httpx.Client | None = None,
) -> int:
    """Fetch `instrument`'s daily candles for `[start, end]` from its
    `price_source` and cache them in `Price`. Native quote currency is stored
    as-is in `Price.ccy` (see `get_close_eur` for conversion). No-op (returns
    `0`) for instruments without a fetchable `price_source` (e.g. `manual`).
    """
    symbol = instrument.price_symbol or instrument.symbol
    if instrument.price_source == "bitstamp":
        rows = bitstamp.fetch_ohlc(symbol, start, end, client=client)
        ccy = "EUR"
    elif instrument.price_source == "stooq":
        rows = [c for c in stooq.fetch_ohlc(symbol, client=client) if start <= c[0] <= end]
        ccy = instrument.quote_ccy
    else:
        return 0

    for on, o, h, low, c in rows:
        _upsert_price(session, instrument.id, on, o, h, low, c, ccy, instrument.price_source)
    session.commit()
    return len(rows)


def ensure_fx(
    session: Session, ccy: str, start: date, end: date, client: httpx.Client | None = None
) -> int:
    """Fetch ECB rates for `ccy` over `[start, end]` and cache them in `FxRate`."""
    ccy = _STABLECOIN_TO_FIAT.get(ccy, ccy)
    if ccy == "EUR":
        return 0
    rates = ecb.fetch_rates(ccy, start, end, client=client)
    for on, rate in rates:
        row = session.execute(
            select(FxRate).where(FxRate.ccy == ccy, FxRate.date == on)
        ).scalar_one_or_none()
        if row is None:
            row = FxRate(ccy=ccy, date=on, rate_to_eur=rate)
            session.add(row)
        else:
            row.rate_to_eur = rate
    session.commit()
    return len(rates)


def fill_pending_eur(session: Session, account_ids: list[int] | None = None) -> int:
    """Value `fx_source == "pending"` transactions, two ways:

    * a priced row (`price` + `price_ccy`) converts at that currency's ECB
      rate and ends up `fx_source="ecb"`;
    * an in-kind row (staking reward, airdrop, crypto-to-crypto swap — a
      quantity and no price at all) is valued off its instrument's own cached
      close and ends up `fx_source="close"`.

    Anything still unvaluable — no rate, no cached close, no instrument —
    stays pending. No exception propagates. Returns the number of rows
    updated.
    """
    stmt = select(Transaction).where(Transaction.fx_source == "pending")
    if account_ids is not None:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))

    updated = 0
    for tx in session.execute(stmt).scalars():
        on = tx.ts.date()
        try:
            fee_fx = get_fx(session, tx.fee_ccy or "EUR", on)
            if tx.price is not None and tx.price_ccy is not None:
                fx_rate: Decimal | None = get_fx(session, tx.price_ccy, on)
                amount_eur = tx.quantity * tx.price * fx_rate
                fx_source = "ecb"
            else:
                instrument = (
                    session.get(Instrument, tx.instrument_id)
                    if tx.instrument_id is not None
                    else None
                )
                close_eur = get_close_eur(session, instrument, on) if instrument else None
                if close_eur is None:
                    continue
                fx_rate, amount_eur, fx_source = None, tx.quantity * close_eur, "close"
        except PriceMissing:
            continue
        tx.amount_eur = amount_eur
        tx.fee_eur = tx.fee * fee_fx
        tx.fx_rate = fx_rate
        tx.fx_source = fx_source
        updated += 1
    session.commit()
    return updated


def candles(session: Session, instrument: Instrument, start: date, end: date) -> list[Candle]:
    """Cached candles for `instrument` over `[start, end]`, oldest first."""
    rows = session.execute(
        select(Price)
        .where(Price.instrument_id == instrument.id, Price.date >= start, Price.date <= end)
        .order_by(Price.date)
    ).scalars()
    return [Candle(date=r.date, open=r.open, high=r.high, low=r.low, close=r.close) for r in rows]
