"""Return math. Two pure functions over `(date, Decimal)` pairs — no session,
no ORM, so both are testable against a hand calculation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from itertools import pairwise

_DAYS_PER_YEAR = Decimal(365)  # Actual/365
_TOLERANCE = Decimal("1e-7")
_RATE_LOW = Decimal("-0.99")
_RATE_HIGH = Decimal(10)
# ponytail: bisection halves the bracket each step, so 200 iterations is far
# past the point where `high - low` drops under the tolerance. It is a
# runaway guard, not a tuning knob.
_MAX_ITERATIONS = 200


def ttwror(values: list[tuple[date, Decimal]], flows: list[tuple[date, Decimal]]) -> Decimal:
    """True time-weighted rate of return: the product of the sub-period
    returns between consecutive valuations, minus 1.

    Sub-periods split at the flow dates. A value on a flow date is taken
    *before* that flow, so the flow belongs to the base of the *next*
    sub-period: `factor = value / (previous_value + flow_on_previous_date)`.

    A flow on the *last* valuation date is therefore ignored — there is no
    sub-period after it for the money to earn a return in, and the final value
    is a pre-flow value like every other. Callers who want that flow to count
    must value the portfolio again after it, i.e. pass a later valuation.
    """
    if len(values) < 2:
        return Decimal(0)

    flow_by_date: dict[date, Decimal] = {}
    for on, amount in flows:
        flow_by_date[on] = flow_by_date.get(on, Decimal(0)) + amount

    points = sorted(values)
    factor = Decimal(1)
    for (previous_date, previous_value), (_, value) in pairwise(points):
        base = previous_value + flow_by_date.get(previous_date, Decimal(0))
        if base == 0:
            continue  # ponytail: nothing invested over this sub-period, so no return to chain
        factor *= value / base
    return factor - 1


def _npv(rate: Decimal, flows: list[tuple[date, Decimal]], start: date) -> Decimal:
    total = Decimal(0)
    for on, amount in flows:
        years = Decimal((on - start).days) / _DAYS_PER_YEAR
        total += amount / (Decimal(1) + rate) ** years
    return total


def xirr(flows: list[tuple[date, Decimal]]) -> Decimal | None:
    """Money-weighted return: the annual rate at which the flows' net present
    value is zero, by bisection on `[-0.99, 10]` with Actual/365 day
    fractions. `None` when the NPV does not change sign over that bracket
    (all-positive or all-negative flows, or a return outside it).
    """
    if len(flows) < 2:
        return None

    ordered = sorted(flows)
    start = ordered[0][0]
    low, high = _RATE_LOW, _RATE_HIGH
    npv_low = _npv(low, ordered, start)
    if npv_low * _npv(high, ordered, start) > 0:
        return None

    rate = low
    for _ in range(_MAX_ITERATIONS):
        rate = (low + high) / 2
        npv_mid = _npv(rate, ordered, start)
        if abs(npv_mid) < _TOLERANCE or high - low < _TOLERANCE:
            return rate
        if npv_low * npv_mid < 0:
            high = rate
        else:
            low, npv_low = rate, npv_mid
    return rate
