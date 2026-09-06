"""Worked examples for the Vorabpauschale formula (Section 18 InvStG)."""

from __future__ import annotations

from decimal import Decimal

from trade_ledger.models import Setting
from trade_ledger.tax.vorabpauschale import BASISZINS, basiszins_for, compute, months_factor

D = Decimal
BZ_2025 = BASISZINS[2025]


def test_full_year_ten_shares():
    # 10 x 100 EUR x 2,53 % x 0,7 = 17,71 EUR, capped by the 100 EUR gain.
    assert compute(D(10), D(100), D(110), D(0), BZ_2025, months_factor(None)) == D("17.71")


def test_bought_in_april_keeps_nine_twelfths():
    assert months_factor(4) == D(9) / 12
    assert compute(D(10), D(100), D(110), D(0), BZ_2025, months_factor(4)) == D("13.28")


def test_price_fell_gives_zero():
    assert compute(D(10), D(100), D(90), D(0), BZ_2025, D(1)) == D(0)


def test_gain_smaller_than_basisertrag_caps_it():
    assert compute(D(10), D(100), D("100.50"), D(0), BZ_2025, D(1)) == D("5.00")


def test_distributions_are_deducted_and_never_go_negative():
    assert compute(D(10), D(100), D(110), D("7.71"), BZ_2025, D(1)) == D("10.00")
    assert compute(D(10), D(100), D(110), D(50), BZ_2025, D(1)) == D(0)


def test_basiszins_setting_overrides_the_table(session):
    assert basiszins_for(session, 2025) == D("0.0253")
    assert basiszins_for(session, 1999) is None
    session.add(Setting(key="basiszins_2025", value="0.0300"))
    session.flush()
    assert basiszins_for(session, 2025) == D("0.0300")
