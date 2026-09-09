from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_ledger.bots import kill_rules, status
from trade_ledger.enums import BotStatus, RunStatus, TradeStatus
from trade_ledger.models import BotRun, BotState, Trade

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
CAPITAL = Decimal(2500)


def _state(session, bot, **kwargs) -> BotState:
    positions = kwargs.pop("positions", None)
    defaults = {"bot_id": bot.id, "ts": NOW, "reconciliation": "ok"}
    defaults.update(kwargs)
    if positions is not None:
        defaults["positions_json"] = json.dumps(positions)
    state = BotState(**defaults)
    session.add(state)
    session.flush()
    return state


def _rule(results: list[dict], name: str) -> dict:
    return next(one for one in results if one["rule"] == name)


# --- K1 capital brake -------------------------------------------------------


def test_k1_triggers_when_equity_falls_to_76_percent_of_stage_capital(session, bot_factory):
    """Worked example: equity 1.900 € of 2.500 € stage capital is 76 %."""
    bot, _ = bot_factory()
    state = _state(session, bot, equity_eur=Decimal(1900), peak_equity_eur=Decimal(2000))

    result = kill_rules.k1_capital(state, CAPITAL)

    assert result["status"] == "triggered"
    assert result["value"] == "76"
    assert result["threshold"] == "80"
    assert result["action"] == "pause"


def test_k1_triggers_on_a_25_percent_drawdown_from_the_peak(session, bot_factory):
    """Worked example: peak 3.000 €, equity 2.250 € — 25 % down, and still
    90 % of stage capital, so only the drawdown limb can fire."""
    bot, _ = bot_factory()
    state = _state(session, bot, equity_eur=Decimal(2250), peak_equity_eur=Decimal(3000))

    result = kill_rules.k1_capital(state, CAPITAL)

    assert result["status"] == "triggered"
    assert result["value"] == "25"
    assert result["threshold"] == "20"


def test_k1_is_ok_above_both_limits(session, bot_factory):
    bot, _ = bot_factory()
    state = _state(session, bot, equity_eur=Decimal(2400), peak_equity_eur=Decimal(2500))

    result = kill_rules.k1_capital(state, CAPITAL)

    assert result["status"] == "ok"
    assert result["value"] == "96"


def test_k1_says_nothing_before_the_first_equity_push():
    assert kill_rules.k1_capital(None, CAPITAL)["status"] == "ok"


def test_k1_warns_when_it_holds_positions_but_reports_no_equity(session, bot_factory):
    """A USDT-quoted account never gets an `equity_eur` — the bot leaves FX to
    the app — so the capital brake is inert while real money is at the venue.
    An `ok` there would be a lie of omission."""
    bot, _ = bot_factory()
    state = _state(session, bot, positions=[{"symbol": "BTC/USDT:USDT", "stop_present": True}])

    result = kill_rules.k1_capital(state, CAPITAL)

    assert (result["status"], result["action"], result["value"]) == ("warning", "alert", None)
    assert "no equity in EUR reported" in result["detail"]

    # No positions and no equity is a bot that has simply not traded yet.
    state.positions_json = "[]"
    assert kill_rules.k1_capital(state, CAPITAL)["status"] == "ok"


# --- K2 rolling edge --------------------------------------------------------


def _closed_trades(session, bot, account, instrument, results: list[Decimal]) -> list[Trade]:
    for index, result in enumerate(results):
        session.add(
            Trade(
                account_id=account.id,
                instrument_id=instrument.id,
                bot_id=bot.id,
                direction="long",
                status=TradeStatus.CLOSED,
                risk_eur=Decimal(10),
                result_eur=result,
                opened_ts=NOW - timedelta(days=len(results) - index),
                closed_ts=NOW - timedelta(hours=len(results) - index),
            )
        )
    session.flush()
    return status.bot_trades(session, bot)


def test_k2_warns_on_a_profit_factor_of_0_8_over_20_trades(
    session, bot_factory, account_factory, instrument_factory
):
    """Worked example: 20 closed trades, all risked 10 €. Four wins of 100 €
    (40 R) against ten losses of 50 € (-50 R) and six scratches — profit
    factor 40/50 = 0,8, below 1,0.
    """
    account = account_factory()
    bot, _ = bot_factory(account_id=account.id)
    instrument = instrument_factory()
    results = (
        [Decimal(100)] * 4  # 400 € won
        + [Decimal(-50)] * 10  # 500 € lost
        + [Decimal(0)] * 6  # scratches, they move neither side
    )
    trades = _closed_trades(session, bot, account, instrument, results)
    assert len(trades) == 20

    result = kill_rules.k2_edge(trades)

    assert result["status"] == "warning"
    assert result["value"] == "0.8"
    assert result["threshold"] == "1"
    assert result["action"] == "alert"


def test_k2_stays_quiet_below_20_closed_trades(
    session, bot_factory, account_factory, instrument_factory
):
    account = account_factory()
    bot, _ = bot_factory(account_id=account.id)
    trades = _closed_trades(
        session, bot, account, instrument_factory(), [Decimal(-50)] * 10 + [Decimal(100)] * 4
    )

    result = kill_rules.k2_edge(trades)

    assert result["status"] == "ok"
    assert result["detail"] == "14 of 20 closed trades so far"


def test_k2_is_ok_with_a_profit_factor_above_one(
    session, bot_factory, account_factory, instrument_factory
):
    account = account_factory()
    bot, _ = bot_factory(account_id=account.id)
    trades = _closed_trades(
        session,
        bot,
        account,
        instrument_factory(),
        [Decimal(100)] * 10 + [Decimal(-50)] * 10,
    )

    assert kill_rules.k2_edge(trades)["status"] == "ok"


# --- K3 streak --------------------------------------------------------------


def test_k3_warns_on_eight_consecutive_losses(
    session, bot_factory, account_factory, instrument_factory
):
    """Worked example: a win, then eight losses in a row."""
    account = account_factory()
    bot, _ = bot_factory(account_id=account.id)
    trades = _closed_trades(
        session, bot, account, instrument_factory(), [Decimal(100)] + [Decimal(-50)] * 8
    )

    result = kill_rules.k3_streak(trades)

    assert result["status"] == "warning"
    assert result["value"] == "8"
    assert result["threshold"] == "8"


def test_k3_counts_back_from_the_newest_trade_only(
    session, bot_factory, account_factory, instrument_factory
):
    """Eight old losses that a win has since ended are history, not a streak."""
    account = account_factory()
    bot, _ = bot_factory(account_id=account.id)
    trades = _closed_trades(
        session, bot, account, instrument_factory(), [Decimal(-50)] * 8 + [Decimal(100)]
    )

    result = kill_rules.k3_streak(trades)

    assert result["status"] == "ok"
    assert result["value"] == "0"


# --- K4 integrity -----------------------------------------------------------


def test_k4_triggers_and_asks_for_flat_on_a_position_without_a_stop(session, bot_factory):
    bot, _ = bot_factory()
    state = _state(
        session,
        bot,
        positions=[
            {"symbol": "BTC/USDT:USDT", "qty": "0.01", "stop_present": True},
            {"symbol": "ETH/USDT:USDT", "qty": "0.1", "stop_present": False},
        ],
    )

    result = kill_rules.k4_integrity(bot, state, None, NOW)

    assert result["status"] == "triggered"
    assert result["action"] == "flat"
    assert "ETH/USDT:USDT" in result["detail"]


def test_k4_triggers_on_a_reconciliation_mismatch(session, bot_factory):
    bot, _ = bot_factory()
    state = _state(session, bot, reconciliation="mismatch", reconciliation_detail="qty differs")

    result = kill_rules.k4_integrity(bot, state, None, NOW)

    assert result["status"] == "triggered"
    assert result["action"] == "alert"
    assert result["detail"] == "qty differs"


@pytest.mark.parametrize(
    ("hours_since_run", "expected_missed"),
    [(1, 0), (5, 1), (9, 2), (13, 3)],
)
def test_missed_runs_counts_the_slots_that_went_by(
    session, bot_factory, hours_since_run, expected_missed
):
    """4-hour bot anchored at 00:05, last run at that anchor."""
    bot, _ = bot_factory(schedule_every_s=14400, schedule_at="00:05")
    started = datetime(2026, 9, 9, 0, 5, tzinfo=UTC)
    run = BotRun(bot_id=bot.id, started=started, status=RunStatus.OK, finished=started)
    session.add(run)
    session.flush()

    missed = kill_rules.missed_runs(bot, run, started + timedelta(hours=hours_since_run))

    assert missed == expected_missed


def test_k4_triggers_after_two_missed_runs(session, bot_factory):
    bot, _ = bot_factory(schedule_every_s=14400, schedule_at="00:05")
    started = datetime(2026, 9, 9, 0, 5, tzinfo=UTC)
    run = BotRun(bot_id=bot.id, started=started, status=RunStatus.OK, finished=started)
    session.add(run)
    session.flush()

    result = kill_rules.k4_integrity(bot, None, run, started + timedelta(hours=9))

    assert result["status"] == "triggered"
    assert result["value"] == "2 missed runs"
    assert result["action"] == "alert"


def test_k4_is_ok_for_a_reconciled_bot_with_stopped_positions(session, bot_factory):
    bot, _ = bot_factory()
    state = _state(session, bot, positions=[{"symbol": "BTC/USDT:USDT", "stop_present": True}])

    assert kill_rules.k4_integrity(bot, state, None, NOW)["status"] == "ok"


# --- K5 heartbeat -----------------------------------------------------------


def test_k5_triggers_on_a_heartbeat_five_hours_old(session, bot_factory):
    """Worked example: every 4 h plus 55 min grace is a 4 h 55 min deadline,
    so a heartbeat five hours old is overdue by five minutes."""
    bot, _ = bot_factory(schedule_every_s=14400, grace_s=3300)
    bot.last_heartbeat = NOW - timedelta(hours=5)

    result = kill_rules.k5_heartbeat(bot, NOW)

    assert result["status"] == "triggered"
    assert result["action"] == "alert"
    assert "overdue by 0:05:00" in result["detail"]


def test_k5_is_ok_inside_the_grace_window(session, bot_factory):
    bot, _ = bot_factory(schedule_every_s=14400, grace_s=3300)
    bot.last_heartbeat = NOW - timedelta(hours=4, minutes=50)

    assert kill_rules.k5_heartbeat(bot, NOW)["status"] == "ok"


# --- evaluate_all and status ------------------------------------------------


def test_evaluate_all_returns_the_five_rules_in_order(session, bot_factory):
    bot, _ = bot_factory()
    bot.last_heartbeat = NOW

    results = kill_rules.evaluate_all(bot, None, [], NOW, CAPITAL)

    assert [one["rule"] for one in results] == ["K1", "K2", "K3", "K4", "K5"]
    assert set(_rule(results, "K1")) == {
        "rule",
        "status",
        "value",
        "threshold",
        "action",
        "detail",
    }


def test_status_priority_disabled_beats_everything(session, bot_factory):
    bot, _ = bot_factory(enabled=False, paused_entries=True)
    bot.last_heartbeat = NOW - timedelta(days=9)

    assert status.derive_status(bot, None, None, NOW) == BotStatus.DISABLED


def test_status_is_stale_before_error(session, bot_factory):
    bot, _ = bot_factory(schedule_every_s=14400, grace_s=3300)
    bot.last_heartbeat = NOW - timedelta(hours=5)
    run = BotRun(bot_id=bot.id, started=NOW, status=RunStatus.ERROR, error="boom")

    assert status.derive_status(bot, None, run, NOW) == BotStatus.STALE


def test_status_error_beats_paused(session, bot_factory):
    bot, _ = bot_factory(paused_entries=True)
    bot.last_heartbeat = NOW
    state = _state(session, bot, reconciliation="mismatch")

    assert status.derive_status(bot, state, None, NOW) == BotStatus.ERROR


def test_status_running_while_a_run_is_open(session, bot_factory):
    bot, _ = bot_factory()
    bot.last_heartbeat = NOW
    run = BotRun(bot_id=bot.id, started=NOW, status=RunStatus.RUNNING)

    assert status.derive_status(bot, None, run, NOW) == BotStatus.RUNNING


def test_status_ok_for_a_healthy_bot(session, bot_factory):
    bot, _ = bot_factory()
    bot.last_heartbeat = NOW
    run = BotRun(bot_id=bot.id, started=NOW, finished=NOW, status=RunStatus.OK)

    assert status.derive_status(bot, None, run, NOW) == BotStatus.OK


def test_bot_trades_returns_only_this_bots_closed_trades(
    session, bot_factory, account_factory, instrument_factory
):
    account = account_factory()
    bot, _ = bot_factory(account_id=account.id)
    other, _ = bot_factory(account_id=account.id)
    instrument = instrument_factory()
    _closed_trades(session, bot, account, instrument, [Decimal(10), Decimal(-5)])
    _closed_trades(session, other, account, instrument, [Decimal(50)])
    session.add(
        Trade(
            account_id=account.id,
            instrument_id=instrument.id,
            bot_id=bot.id,
            direction="long",
            status=TradeStatus.OPEN,
            risk_eur=Decimal(10),
        )
    )
    session.flush()

    rows = status.bot_trades(session, bot)

    assert [one.result_eur for one in rows] == [Decimal(10), Decimal(-5)]


def test_a_slot_is_not_missed_until_the_grace_period_is_over(session, bot_factory):
    """4-hour bot, 55 min grace: at 04:10 the 04:05 slot is five minutes
    late, not missed. It only counts from 05:00.
    """
    bot, _ = bot_factory(schedule_every_s=14400, schedule_at="00:05", grace_s=3300)
    started = datetime(2026, 9, 9, 0, 5, tzinfo=UTC)
    run = BotRun(bot_id=bot.id, started=started, status=RunStatus.OK, finished=started)
    session.add(run)
    session.flush()

    assert kill_rules.missed_runs(bot, run, datetime(2026, 9, 9, 4, 10, tzinfo=UTC)) == 0
    assert kill_rules.missed_runs(bot, run, datetime(2026, 9, 9, 5, 0, tzinfo=UTC)) == 1
