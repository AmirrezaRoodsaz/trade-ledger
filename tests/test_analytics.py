from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_ledger.engine import analytics
from trade_ledger.enums import Direction, TradeStatus
from trade_ledger.models import Trade

RISK = Decimal(50)


def _trade(
    r,
    closed_ts,
    *,
    opened_ts=None,
    adherence=None,
    mistake=None,
    tags="[]",
    emotion_post=None,
    mae_r=None,
    mfe_r=None,
    risk=RISK,
):
    """A closed `Trade` built in memory, no DB involved — the engine reads
    plain attributes, not rows from a session.
    """
    r = Decimal(r)
    return Trade(
        account_id=1,
        instrument_id=1,
        direction=Direction.LONG,
        status=TradeStatus.CLOSED,
        risk_eur=risk,
        result_eur=r * risk,
        mae_eur=(Decimal(mae_r) * risk) if mae_r is not None else None,
        mfe_eur=(Decimal(mfe_r) * risk) if mfe_r is not None else None,
        opened_ts=opened_ts or (closed_ts - timedelta(hours=5)),
        closed_ts=closed_ts,
        adherence=adherence,
        mistake=mistake,
        tags=tags,
        emotion_post=emotion_post,
    )


def _ts(day: int) -> datetime:
    return datetime(2026, 1, day, 12, 0, tzinfo=UTC)  # 2026-01-05 is a Monday


def _fixture() -> list[Trade]:
    """The brief's 6-trade fixture: R = +2, -1, +3, -1, -1, +0.5, risk 50
    each, closed on six consecutive days (Mon..Sat).
    """
    rs = ["2", "-1", "3", "-1", "-1", "0.5"]
    return [_trade(r, _ts(5 + i)) for i, r in enumerate(rs)]


def test_closed_filters_and_sorts():
    trades = [
        _trade("1", _ts(10)),
        _trade("1", _ts(5)),
        Trade(account_id=1, instrument_id=1, direction=Direction.LONG, status=TradeStatus.OPEN),
        _trade("1", _ts(1), risk=Decimal(0)),  # zero risk excluded
    ]
    result = analytics.closed(trades)
    assert [t.closed_ts for t in result] == [_ts(5), _ts(10)]


def test_stats_match_the_fixture():
    stats = analytics.compute_stats(_fixture())

    assert (stats.count, stats.wins, stats.losses, stats.flat) == (6, 3, 3, 0)
    assert stats.win_rate == Decimal("0.5")
    # Mean R, matching the money side's `trade_stats.py` (`expectancy = total_r / n`).
    assert stats.expectancy_r == Decimal("2.5") / 6
    assert stats.total_r == Decimal("2.5")
    assert stats.total_eur == Decimal(125)
    assert stats.avg_win_r == Decimal("5.5") / 3
    assert stats.avg_loss_r == Decimal(-1)
    assert stats.profit_factor == Decimal("5.5") / 3
    assert stats.payoff == stats.profit_factor  # avg loss is exactly -1R here
    # Chronological cumulative R is 2, 1, 4, 3, 2, 2.5 -> peak 4, trough 2.
    assert stats.max_dd_r == Decimal(2)
    assert stats.max_dd_eur == Decimal(100)
    assert stats.recovery_factor == Decimal("1.25")
    assert stats.max_win_streak == 1
    assert stats.max_loss_streak == 2
    assert stats.std_r is not None
    assert stats.sqn.quantize(Decimal("0.001")) == Decimal("0.585")
    assert stats.adherence is None
    assert stats.cost_of_mistakes_r == Decimal(0)
    assert stats.mistakes == {}


def test_stats_on_empty_list_is_zeroed_not_crashing():
    stats = analytics.compute_stats([])
    assert stats.count == 0
    assert stats.win_rate == Decimal(0)
    assert stats.profit_factor is None
    assert stats.sqn is None


def test_sqn_and_std_none_below_two_trades():
    stats = analytics.compute_stats([_trade("1", _ts(1))])
    assert stats.sqn is None
    assert stats.std_r is None


def test_all_wins_gives_no_profit_factor_or_payoff():
    """Both ratios divide by the loss side; with no losses there's nothing
    to divide by, so both stay `None` rather than reporting a fake infinity.
    """
    stats = analytics.compute_stats([_trade("1", _ts(1)), _trade("2", _ts(2))])
    assert stats.profit_factor is None
    assert stats.payoff is None
    assert stats.avg_loss_r == Decimal(0)


def test_adherence_mistakes_mae_mfe_and_capture():
    trades = [
        _trade("2", _ts(1), adherence=True, mae_r="-0.5", mfe_r="3"),
        _trade("-1", _ts(2), adherence=False, mistake="moved_stop", mae_r="-1.2", mfe_r="0.3"),
        _trade("1", _ts(3), mistake="fomo"),  # adherence not reviewed
    ]
    stats = analytics.compute_stats(trades)

    assert stats.adherence == Decimal(1) / 2  # only the two reviewed trades count
    assert stats.mistakes == {"moved_stop": 1, "fomo": 1}
    assert stats.cost_of_mistakes_r == Decimal(-1) + Decimal(1)
    assert stats.avg_mae_r == (Decimal("-0.5") + Decimal("-1.2")) / 2
    assert stats.avg_mfe_r == (Decimal(3) + Decimal("0.3")) / 2
    # capture = mean(result_r / mfe_r) over trades with mfe_r > 0 -> both trades qualify.
    assert stats.capture == (Decimal(2) / Decimal(3) + Decimal(-1) / Decimal("0.3")) / 2


def test_avg_hold_hours_win_and_loss():
    win = _trade("1", _ts(2), opened_ts=_ts(1))  # 24h
    loss = _trade("-1", _ts(3), opened_ts=_ts(2) + timedelta(hours=12))  # 12h
    stats = analytics.compute_stats([win, loss])
    assert stats.avg_hold_hours_win == Decimal(24)
    assert stats.avg_hold_hours_loss == Decimal(12)


def test_avg_hold_hours_has_no_float_noise():
    """`timedelta.total_seconds()` returns a `float`; 45.123456 isn't exactly
    representable in binary, so routing a hold time through it (via
    `Decimal(delta.total_seconds())`) used to leak dozens of spurious digits
    past the microsecond precision the input actually has. The integer-based
    formula must not.
    """
    delta = timedelta(hours=1, minutes=23, seconds=45, microseconds=123456)
    opened = _ts(1)
    stats = analytics.compute_stats([_trade("1", opened + delta, opened_ts=opened)])

    # Same integer arithmetic as the implementation, computed independently.
    seconds = Decimal(delta.days) * 86400 + Decimal(delta.seconds) + Decimal(delta.microseconds) / 1_000_000
    expected = seconds / 3600
    assert stats.avg_hold_hours_win == expected

    # The pre-division seconds value is where the old float bug would show:
    # exactly 6 fractional digits here, not dozens of binary-noise digits.
    fractional_digits = str(seconds).partition(".")[2]
    assert seconds == Decimal("5025.123456")
    assert len(fractional_digits) <= 6


def test_breakdown_by_weekday_keys_are_english_abbreviations():
    result = analytics.breakdown(_fixture(), "weekday")
    assert set(result) == {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat"}
    assert result["Mon"].count == 1
    assert result["Mon"].total_r == Decimal(2)


def test_breakdown_by_month():
    trades = [_trade("1", datetime(2026, 1, 5, tzinfo=UTC)), _trade("1", datetime(2026, 2, 5, tzinfo=UTC))]
    result = analytics.breakdown(trades, "month")
    assert set(result) == {"2026-01", "2026-02"}


def test_breakdown_by_hold_bucket():
    same_day = _trade("1", _ts(2), opened_ts=_ts(2) - timedelta(hours=2))
    two_weeks = _trade("1", _ts(20), opened_ts=_ts(20) - timedelta(days=10))
    result = analytics.breakdown([same_day, two_weeks], "hold_bucket")
    assert result["<1d"].count == 1
    assert result["1–4w"].count == 1


def test_breakdown_by_emotion_defaults_to_none():
    trades = [_trade("1", _ts(1), emotion_post="calm"), _trade("-1", _ts(2))]
    result = analytics.breakdown(trades, "emotion")
    assert set(result) == {"calm", "none"}


def test_breakdown_by_tag_counts_every_tag_a_trade_carries():
    trades = [
        _trade("1", _ts(1), tags='["breakout", "fomo"]'),
        _trade("-1", _ts(2), tags='["fomo"]'),
    ]
    result = analytics.breakdown(trades, "tag")
    assert set(result) == {"breakout", "fomo"}
    assert result["breakout"].count == 1
    assert result["fomo"].count == 2


def test_breakdown_uses_attached_names_for_playbook_instrument_account():
    trade = _trade("1", _ts(1))
    trade.instrument_symbol = "BTCEUR"
    trade.account_name = "okx-paper"
    trade.playbook_label = "Turtle v2"
    other = _trade("1", _ts(2))  # no attributes attached -> falls back

    assert set(analytics.breakdown([trade, other], "instrument")) == {"BTCEUR", "unknown"}
    assert set(analytics.breakdown([trade, other], "account")) == {"okx-paper", "unknown"}
    assert set(analytics.breakdown([trade, other], "playbook")) == {"Turtle v2", "none"}


def test_breakdown_rejects_unknown_key():
    with pytest.raises(ValueError, match="unknown breakdown key"):
        analytics.breakdown(_fixture(), "nonsense")


def test_equity_curve_tracks_cumulative_and_drawdown():
    curve = analytics.equity_curve(_fixture())
    assert [p["cum_r"] for p in curve] == [
        Decimal(2),
        Decimal(1),
        Decimal(4),
        Decimal(3),
        Decimal(2),
        Decimal("2.5"),
    ]
    assert curve[4]["dd_r"] == Decimal(2)
    assert curve[4]["dd_eur"] == Decimal(100)
    assert curve[0]["date"] == _ts(5).date()


def test_r_histogram_bins():
    bins = analytics.r_histogram(_fixture())
    assert bins == [
        {"lo": Decimal("-1.0"), "hi": Decimal("-0.5"), "count": 3},
        {"lo": Decimal("0.5"), "hi": Decimal("1.0"), "count": 1},
        {"lo": Decimal("2.0"), "hi": Decimal("2.5"), "count": 1},
        {"lo": Decimal("3.0"), "hi": Decimal("3.5"), "count": 1},
    ]


def test_calendar_sums_by_close_date_and_ignores_other_months():
    trades = [
        _trade("1", datetime(2026, 1, 5, 10, tzinfo=UTC)),
        _trade("-1", datetime(2026, 1, 5, 15, tzinfo=UTC)),
        _trade("1", datetime(2026, 2, 1, tzinfo=UTC)),
    ]
    days = analytics.calendar(trades, 2026, 1)
    assert days == {"2026-01-05": {"count": 2, "r": Decimal(0), "eur": Decimal(0)}}


def test_stage_gate_fails_on_count_with_a_clear_reason():
    result = analytics.stage_gate(_fixture(), "paper", Decimal(2500))
    assert result["passed"] is False
    count_check = result["checks"][0]
    assert count_check["passed"] is False
    assert count_check["actual"] == 6
    assert count_check["required"] == 30
    assert "30" in count_check["name"] and "closed trades" in count_check["name"]
    # Expectancy and drawdown are fine on this fixture; only count and adherence fail.
    assert result["checks"][1]["passed"] is True
    assert result["checks"][3]["passed"] is True


def test_stage_gate_passes_when_every_check_clears():
    trades = [_trade("1", _ts(1) + timedelta(days=i), adherence=True) for i in range(30)]
    result = analytics.stage_gate(trades, "paper", Decimal(2500))
    assert result["passed"] is True
    assert all(c["passed"] for c in result["checks"])


def test_stage_gate_rejects_unknown_mode():
    with pytest.raises(ValueError, match="paper.*live"):
        analytics.stage_gate(_fixture(), "all", Decimal(2500))


# --- API ---------------------------------------------------------------


def _plan(client, account, instrument, **overrides):
    payload = {
        "account_id": account.id,
        "instrument_id": instrument.id,
        "direction": "long",
        "planned_entry": "100",
        "planned_stop": "95",
        "risk_eur": "50",
        "note_pre": "range breakout",
    }
    payload.update(overrides)
    return client.post("/api/trades", json=payload).json()


def _close(client, trade, open_ts, close_ts, price):
    client.post(
        f"/api/trades/{trade['id']}/open",
        json={"manual": {"ts": open_ts, "quantity": "10", "price": "100", "fee_eur": "0"}},
    )
    return client.post(
        f"/api/trades/{trade['id']}/close",
        json={"manual": {"ts": close_ts, "quantity": "10", "price": price, "fee_eur": "0"}},
    ).json()


def test_analytics_routes_smoke(client, account_factory, instrument_factory):
    account, instrument = account_factory(), instrument_factory()
    trade = _plan(client, account, instrument)
    closed = _close(client, trade, "2026-01-05T10:00:00Z", "2026-01-06T10:00:00Z", "120")
    assert closed["status"] == "closed"

    stats = client.get("/api/analytics/stats").json()
    assert stats["count"] == 1
    assert stats["total_eur"] == "200"  # (120-100)*10, zero fees

    breakdown = client.get("/api/analytics/breakdown", params={"by": "instrument"}).json()
    assert instrument.symbol in breakdown

    assert client.get("/api/analytics/breakdown", params={"by": "nonsense"}).status_code == 422

    equity = client.get("/api/analytics/equity").json()
    assert len(equity) == 1

    histogram = client.get("/api/analytics/histogram").json()
    assert sum(b["count"] for b in histogram) == 1

    calendar = client.get("/api/analytics/calendar", params={"year": 2026, "month": 1}).json()
    assert calendar["2026-01-06"]["count"] == 1

    gate = client.get("/api/analytics/stage-gate", params={"mode": "paper"}).json()
    assert gate["capital"] == "2500"  # default Setting stage_capital_paper
    assert gate["passed"] is False

    assert client.get("/api/analytics/stage-gate", params={"mode": "all"}).status_code == 422


def test_stage_gate_capital_setting_override(client, account_factory, instrument_factory):
    client.put("/api/settings", json=[{"key": "stage_capital_live", "value": "250"}])
    gate = client.get("/api/analytics/stage-gate", params={"mode": "live"}).json()
    assert gate["capital"] == "250"
    explicit = client.get(
        "/api/analytics/stage-gate", params={"mode": "live", "capital": "500"}
    ).json()
    assert explicit["capital"] == "500"
