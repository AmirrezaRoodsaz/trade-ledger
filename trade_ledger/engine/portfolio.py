"""Portfolio state over the ledger: holdings, cash flows, valuation,
allocation, dividends and fees.

Everything here is a read over `Transaction` rows plus the cached prices, so
it stays testable without HTTP. Costs use a running average per instrument —
that is a *display* figure; the tax engine does its own FIFO.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..enums import TxType
from ..ledger import cash_balance, cash_delta_eur
from ..models import Account, Instrument, Price, Transaction
from ..prices.service import PriceMissing, get_close_eur

# Types that add quantity at cost. SPLIT adds quantity at zero cost.
_COST_IN = (TxType.BUY, TxType.TRANSFER_IN, TxType.STAKING_REWARD, TxType.AIRDROP)
_COST_OUT = (TxType.SELL, TxType.TRANSFER_OUT)

_CASHFLOW_TYPES = (
    TxType.DEPOSIT,
    TxType.WITHDRAWAL,
    TxType.DIVIDEND,
    TxType.INTEREST,
    TxType.FEE,
)


@dataclass
class Holding:
    instrument: Instrument
    quantity: Decimal
    avg_cost_eur: Decimal
    cost_eur: Decimal
    price_eur: Decimal | None
    value_eur: Decimal | None
    unrealised_eur: Decimal | None
    weight: Decimal


def start_of_day(on: date) -> datetime:
    """The first instant of `on`, so `ts >= from` includes the whole day."""
    return datetime.combine(on, time.min, tzinfo=UTC)


def end_of_day(on: date) -> datetime:
    """The last instant of `on`, so `ts <= at` covers the whole day."""
    return datetime.combine(on, time.max, tzinfo=UTC)


def _price_eur(session: Session, instrument: Instrument, on: date) -> Decimal | None:
    """Close in EUR from the newest cached price on or before `on`, or `None`.

    Never `0` for a missing price: a zero would quietly read as a worthless
    holding instead of an unknown one.
    """
    latest = session.execute(
        select(func.max(Price.date)).where(
            Price.instrument_id == instrument.id,
            Price.date <= on,
            Price.close.isnot(None),
        )
    ).scalar()
    if latest is None:
        return None
    try:
        return get_close_eur(session, instrument, latest)
    except PriceMissing:
        return None  # no FX rate for the quote currency — unknown, not zero


def holdings(session: Session, account_ids: list[int], at: datetime | None = None) -> list[Holding]:
    """Open positions across `account_ids` as of `at` (default: now), with a
    running-average cost, the latest known price and portfolio weights.

    Weights are shares of the total value of the holdings that *have* a
    price; a holding without one gets weight `0` rather than distorting the
    others.
    """
    at = at or datetime.now(UTC)
    rows = (
        session.execute(
            select(Transaction)
            .where(
                Transaction.account_id.in_(account_ids),
                Transaction.instrument_id.isnot(None),
                Transaction.ts <= at,
            )
            .order_by(Transaction.ts, Transaction.id)
        )
        .scalars()
        .all()
    )

    quantities: dict[int, Decimal] = {}
    costs: dict[int, Decimal] = {}
    for tx in rows:
        key = tx.instrument_id
        quantity = quantities.get(key, Decimal(0))
        cost = costs.get(key, Decimal(0))
        if tx.type in _COST_IN:
            quantities[key] = quantity + tx.quantity
            costs[key] = cost + tx.amount_eur + tx.fee_eur
        elif tx.type in _COST_OUT:
            if quantity > 0:
                costs[key] = cost - cost * tx.quantity / quantity
            quantities[key] = quantity - tx.quantity
        elif tx.type == TxType.SPLIT:
            quantities[key] = quantity + tx.quantity

    result: list[Holding] = []
    for instrument_id, quantity in quantities.items():
        if quantity == 0:
            continue
        instrument = session.get(Instrument, instrument_id)
        cost = costs.get(instrument_id, Decimal(0))
        price = _price_eur(session, instrument, at.date())
        value = None if price is None else quantity * price
        result.append(
            Holding(
                instrument=instrument,
                quantity=quantity,
                avg_cost_eur=cost / quantity,
                cost_eur=cost,
                price_eur=price,
                value_eur=value,
                unrealised_eur=None if value is None else value - cost,
                weight=Decimal(0),
            )
        )

    total = sum((h.value_eur for h in result if h.value_eur is not None), Decimal(0))
    if total:
        for holding in result:
            if holding.value_eur is not None:
                holding.weight = holding.value_eur / total
    result.sort(key=lambda h: h.instrument.symbol)
    return result


def cashflows(
    session: Session, account_ids: list[int], date_from: date, date_to: date
) -> list[dict]:
    """Deposits, withdrawals, dividends, interest and fees over the period,
    oldest first. `amount_eur` is the signed effect on cash (`cash_delta_eur`),
    so withdrawals and fees are negative.
    """
    rows = (
        session.execute(
            select(Transaction)
            .where(
                Transaction.account_id.in_(account_ids),
                Transaction.type.in_(_CASHFLOW_TYPES),
                Transaction.ts >= start_of_day(date_from),
                Transaction.ts <= end_of_day(date_to),
            )
            .order_by(Transaction.ts, Transaction.id)
        )
        .scalars()
        .all()
    )
    names = dict(session.execute(select(Account.id, Account.name)).all())
    return [
        {
            "date": tx.ts.date(),
            "type": tx.type,
            "amount_eur": cash_delta_eur(tx),
            "account": names.get(tx.account_id, ""),
        }
        for tx in rows
    ]


def external_flows(
    session: Session, account_ids: list[int], date_from: date, date_to: date
) -> list[tuple[date, Decimal]]:
    """Money in and out of the portfolio as a whole: deposits `+`,
    withdrawals `−`, on `amount_eur`. Fees, dividends and interest are
    internal — they change the value, they are not new capital.
    """
    flows: list[tuple[date, Decimal]] = []
    for tx in (
        session.execute(
            select(Transaction)
            .where(
                Transaction.account_id.in_(account_ids),
                Transaction.type.in_((TxType.DEPOSIT, TxType.WITHDRAWAL)),
                Transaction.ts >= start_of_day(date_from),
                Transaction.ts <= end_of_day(date_to),
            )
            .order_by(Transaction.ts, Transaction.id)
        )
        .scalars()
        .all()
    ):
        sign = 1 if tx.type == TxType.DEPOSIT else -1
        flows.append((tx.ts.date(), sign * tx.amount_eur))
    return flows


def portfolio_value(session: Session, account_ids: list[int], on: date) -> Decimal:
    """Cash plus the EUR value of every holding at the end of `on`.

    Raises `PriceMissing` if any held instrument has no usable price — a
    partial total would silently understate the portfolio.
    """
    at = end_of_day(on)
    total = sum((cash_balance(session, i, at) for i in account_ids), Decimal(0))
    for holding in holdings(session, account_ids, at):
        if holding.value_eur is None:
            raise PriceMissing(f"no price for {holding.instrument.symbol} on {on}")
        total += holding.value_eur
    return total


def value_series(
    session: Session,
    account_ids: list[int],
    date_from: date,
    date_to: date,
    step_days: int = 1,
) -> list[tuple[date, Decimal]]:
    """`portfolio_value` every `step_days` over `[date_from, date_to]`. Dates
    with a missing price are skipped, not zeroed; callers that care can diff
    the returned dates against the grid.
    """
    series: list[tuple[date, Decimal]] = []
    on = date_from
    while on <= date_to:
        try:
            series.append((on, portfolio_value(session, account_ids, on)))
        except PriceMissing:
            pass
        on += timedelta(days=step_days)
    return series


def allocation(session: Session, account_ids: list[int], by: str) -> list[dict]:
    """Current holdings grouped by `asset_class`, `venue` or `instrument`,
    largest first. Weights are over the grouped value, so they sum to 1.
    """
    if by not in ("asset_class", "venue", "instrument"):
        raise ValueError("by must be one of asset_class, venue, instrument")

    totals: dict[str, Decimal] = {}

    def add(key: str, value: Decimal) -> None:
        totals[key] = totals.get(key, Decimal(0)) + value

    if by == "venue":
        # ponytail: a holding spans accounts, so venue needs one pass per account.
        for account_id in account_ids:
            account = session.get(Account, account_id)
            for holding in holdings(session, [account_id]):
                if holding.value_eur is not None:
                    add(account.venue, holding.value_eur)
    else:
        for holding in holdings(session, account_ids):
            if holding.value_eur is not None:
                key = (
                    holding.instrument.asset_class
                    if by == "asset_class"
                    else holding.instrument.symbol
                )
                add(key, holding.value_eur)

    total = sum(totals.values(), Decimal(0))
    return [
        {"key": key, "value_eur": value, "weight": value / total if total else Decimal(0)}
        for key, value in sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    ]


def _dividend_rows(
    session: Session, account_ids: list[int], date_from: date, date_to: date
) -> list[Transaction]:
    return list(
        session.execute(
            select(Transaction)
            .where(
                Transaction.account_id.in_(account_ids),
                Transaction.type == TxType.DIVIDEND,
                Transaction.ts >= start_of_day(date_from),
                Transaction.ts <= end_of_day(date_to),
            )
            .order_by(Transaction.ts, Transaction.id)
        )
        .scalars()
        .all()
    )


def dividends(session: Session, account_ids: list[int], year: int) -> dict:
    """Dividend income for `year`, split by instrument and by month.

    `projected_next_12m` is the last twelve months' dividends of the
    instruments still held at year end — a naive run-rate, not a forecast.
    """
    start, end = date(year, 1, 1), date(year, 12, 31)
    rows = _dividend_rows(session, account_ids, start, end)

    by_instrument: dict[str, Decimal] = {}
    by_month: dict[str, Decimal] = {}
    total = Decimal(0)
    withholding = Decimal(0)
    for tx in rows:
        total += tx.amount_eur
        withholding += tx.withholding_tax_eur
        symbol = session.get(Instrument, tx.instrument_id).symbol if tx.instrument_id else "—"
        by_instrument[symbol] = by_instrument.get(symbol, Decimal(0)) + tx.amount_eur
        month = tx.ts.date().strftime("%Y-%m")
        by_month[month] = by_month.get(month, Decimal(0)) + tx.amount_eur

    # The twelve months ending on 31 December are the calendar year itself, so
    # the run-rate is this year's dividends minus those of positions since sold.
    held = {
        h.instrument.id for h in holdings(session, account_ids, end_of_day(end)) if h.quantity > 0
    }
    projected = sum((tx.amount_eur for tx in rows if tx.instrument_id in held), Decimal(0))
    return {
        "total": total,
        "withholding": withholding,
        "by_instrument": by_instrument,
        "by_month": by_month,
        "projected_next_12m": projected,
    }


def fees(session: Session, account_ids: list[int], year: int) -> dict:
    """All costs charged in `year` per account: the `fee_eur` carried on any
    transaction plus standalone `fee` transactions.
    """
    start, end = date(year, 1, 1), date(year, 12, 31)
    rows = (
        session.execute(
            select(Transaction)
            .where(
                Transaction.account_id.in_(account_ids),
                Transaction.ts >= start_of_day(start),
                Transaction.ts <= end_of_day(end),
            )
            .order_by(Transaction.ts, Transaction.id)
        )
        .scalars()
        .all()
    )
    names = dict(session.execute(select(Account.id, Account.name)).all())

    by_account: dict[str, Decimal] = {}
    for tx in rows:
        amount = tx.fee_eur + (tx.amount_eur if tx.type == TxType.FEE else Decimal(0))
        if amount == 0:
            continue
        name = names.get(tx.account_id, "")
        by_account[name] = by_account.get(name, Decimal(0)) + amount
    return {"by_account": by_account, "total": sum(by_account.values(), Decimal(0))}
