from __future__ import annotations

from datetime import date
from decimal import Decimal

from trade_ledger.engine.returns import ttwror, xirr


def test_xirr_of_one_year_ten_percent_gain():
    rate = xirr([(date(2025, 1, 1), Decimal(-1000)), (date(2026, 1, 1), Decimal(1100))])

    assert rate is not None
    assert round(rate, 3) == Decimal("0.100")


def test_xirr_is_none_without_a_sign_change():
    assert xirr([(date(2025, 1, 1), Decimal(100)), (date(2026, 1, 1), Decimal(200))]) is None


def test_xirr_is_none_for_a_single_flow():
    assert xirr([(date(2025, 1, 1), Decimal(-1000))]) is None


def test_ttwror_with_a_mid_period_deposit_matches_the_hand_calculation():
    # 1.000 grows to 1.100 (+10 %), then 900 is added on the same day; the
    # 2.000 base grows to 2.200 (+10 %). Chained: 1,1 * 1,1 - 1 = 0,21.
    values = [
        (date(2026, 1, 1), Decimal(1000)),
        (date(2026, 7, 1), Decimal(1100)),  # before the flow
        (date(2026, 12, 31), Decimal(2200)),
    ]
    flows = [(date(2026, 7, 1), Decimal(900))]

    assert ttwror(values, flows) == Decimal("0.21")


def test_ttwror_ignores_a_flow_on_the_last_valuation_date():
    values = [(date(2026, 1, 1), Decimal(1000)), (date(2026, 12, 31), Decimal(1100))]

    assert ttwror(values, [(date(2026, 12, 31), Decimal(500))]) == Decimal("0.1")


def test_ttwror_of_a_single_valuation_is_zero():
    assert ttwror([(date(2026, 1, 1), Decimal(1000))], []) == Decimal(0)
