"""Donchian on synthetic 4-hour bars, with every number worked by hand.

The base series is deliberately flat and identical bar to bar — high 155,
low 145, close 150 — so its true range is 10 on every bar and Wilder's ATR
is exactly 10 no matter how it is seeded. That makes the stop arithmetic
checkable without a spreadsheet.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_ledger.botkit.strategies import DEFAULT_PARAMS, STRATEGIES, Signal
from trade_ledger.botkit.strategies.donchian import atr, default_params, signals
from trade_ledger.prices.service import Candle

START = datetime(2026, 1, 1, tzinfo=UTC)


def _bar(index: int, o, h, low, c) -> Candle:
    return Candle(
        date=START + timedelta(hours=4 * index),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
    )


def _flat(count: int, start_index: int = 0) -> list[Candle]:
    """`count` identical bars: 150 / 155 / 145 / 150, true range 10."""
    return [_bar(start_index + i, 150, 155, 145, 150) for i in range(count)]


# -- ATR ----------------------------------------------------------------------


def test_atr_is_wilder_smoothed_not_a_plain_mean():
    # 21 flat bars -> 20 true ranges of 10 -> seed ATR = 10.
    # Bar 21 (high 180, low 150 against a previous close of 150) has a true
    # range of 30: (10 * 19 + 30) / 20 = 11.
    # Bar 22 is flat again, true range 10: (11 * 19 + 10) / 20 = 10.95 --
    # a rolling 20-bar mean of the same ranges would say 11.00.
    seeded = _flat(21)
    spiked = seeded + [_bar(21, 150, 180, 150, 170)]
    settled = spiked + [_bar(22, 170, 180, 170, 175)]

    assert atr(seeded, 20) == Decimal(10)
    assert atr(spiked, 20) == Decimal(11)
    assert atr(settled, 20) == Decimal("10.95")


def test_atr_none_when_history_too_short():
    assert atr(_flat(20), 20) is None  # 20 bars -> only 19 true ranges


# -- entries ------------------------------------------------------------------


def test_no_entry_while_flat_inside_the_channel():
    bars = _flat(60)  # last close 150, 55-bar high 155

    assert signals({"BTC/USDT:USDT": bars}, {}, {}) == []


def test_breakout_at_bar_60_enters_at_the_close_with_a_two_atr_stop():
    # Bars 0..59 are flat (high 155). Bar 60 closes at 160, above the highest
    # high of the 55 bars before it, and has a true range of exactly 10
    # (160 - 150), so ATR(20) stays 10 and the stop is 160 - 2 * 10.
    bars = _flat(60) + [_bar(60, 150, 160, 150, 160)]

    out = signals({"BTC/USDT:USDT": bars}, {}, {})

    assert len(out) == 1
    signal = out[0]
    assert isinstance(signal, Signal)
    assert (signal.symbol, signal.side) == ("BTC/USDT:USDT", "buy")
    assert signal.entry == Decimal(160)
    assert signal.stop == Decimal(140)
    assert "55-bar high 155" in signal.reason


def test_close_equal_to_the_channel_high_is_not_a_breakout():
    bars = _flat(60) + [_bar(60, 150, 155, 150, 155)]

    assert signals({"BTC/USDT:USDT": bars}, {}, {}) == []


def test_no_entry_for_a_symbol_already_held():
    bars = _flat(60) + [_bar(60, 150, 160, 150, 160)]

    out = signals({"BTC/USDT:USDT": bars}, {}, {"BTC/USDT:USDT": {"qty": "0.01"}})

    assert out == []  # held and still above the 20-bar low: nothing to do


def test_too_little_history_yields_no_signal():
    assert signals({"BTC/USDT:USDT": _flat(30)}, {}, {}) == []


# -- exits --------------------------------------------------------------------


def test_exit_when_close_breaks_the_20_bar_low():
    # The 20 bars before the last one all have low 145; the last closes at 140.
    bars = _flat(61) + [_bar(61, 145, 150, 138, 140)]

    out = signals({"BTC/USDT:USDT": bars}, {}, {"BTC/USDT:USDT": {"qty": "0.01"}})

    assert len(out) == 1
    assert (out[0].side, out[0].entry, out[0].stop) == ("sell", None, None)
    assert "20-bar low 145" in out[0].reason


def test_no_exit_while_the_close_holds_the_channel():
    bars = _flat(62)

    assert signals({"BTC/USDT:USDT": bars}, {}, {"BTC/USDT:USDT": {"qty": "0.01"}}) == []


# -- params and registry ------------------------------------------------------


def test_params_override_the_defaults():
    # entry=10 makes the same flat series a breakout at 151 instead of 156.
    bars = _flat(30) + [_bar(30, 150, 156, 150, 156)]

    assert signals({"X": bars}, {"entry": 10}, {}) != []
    assert signals({"X": bars}, {}, {}) == []  # 55-bar channel: too little history


def test_registry_exposes_donchian_and_its_defaults():
    assert STRATEGIES["donchian"] is signals
    assert DEFAULT_PARAMS["donchian"] == default_params()
    assert default_params() == {"entry": 55, "exit": 20, "atr_len": 20, "atr_mult": 2}


def test_signals_are_decimal_only():
    bars = _flat(60) + [_bar(60, 150, 160, 150, 160)]

    signal = signals({"BTC/USDT:USDT": bars}, {}, {})[0]

    assert isinstance(signal.entry, Decimal) and isinstance(signal.stop, Decimal)
