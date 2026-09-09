from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from trade_ledger.bots import readiness as engine
from trade_ledger.enums import Direction, Mode, TradeStatus
from trade_ledger.models import Account, BacktestResult, Setting, Trade

RISK = Decimal(50)


@pytest.fixture()
def bot(bot_factory):
    made, _token = bot_factory()
    return made


@pytest.fixture()
def paper_account(session, bot):
    return session.get(Account, bot.account_id)


def _backtest(session, **over) -> BacktestResult:
    values = {
        "strategy": "donchian",
        "label": "donchian 4h 2020-2026",
        "period_start": date(2020, 1, 1),
        "period_end": date(2026, 1, 1),
        "trades": 45,
        "expectancy_r": Decimal("0.18"),
        "profit_factor": Decimal("1.8"),
        "win_rate": Decimal("0.42"),
        "max_drawdown_pct": Decimal(21),
        "passed": True,
    }
    values.update(over)
    row = BacktestResult(**values)
    session.add(row)
    session.commit()
    return row


def _trades(session, bot, account, instrument, count, *, r="1", adherence=True) -> None:
    """`count` closed bot trades on one account — winners by default, so the
    stage gate fails only on what a test makes it fail on.
    """
    start = datetime(2026, 1, 1, 12, tzinfo=UTC)
    for i in range(count):
        session.add(
            Trade(
                account_id=account.id,
                instrument_id=instrument.id,
                bot_id=bot.id,
                direction=Direction.LONG,
                status=TradeStatus.CLOSED,
                risk_eur=RISK,
                result_eur=Decimal(r) * RISK,
                opened_ts=start + timedelta(days=i),
                closed_ts=start + timedelta(days=i, hours=6),
                adherence=adherence,
            )
        )
    session.commit()


def _stage(result: dict, key: str) -> dict:
    return next(stage for stage in result["stages"] if stage["key"] == key)


# --- readiness --------------------------------------------------------------


def test_nothing_done_is_zero(session, bot):
    result = engine.readiness(session, bot)
    assert result["percent"] == Decimal("0.0")
    assert [stage["key"] for stage in result["stages"]] == [
        "backtest",
        "drills",
        "incubation",
        "live",
    ]


def test_backtest_and_half_the_demo_trades_are_40_percent(
    session, bot, paper_account, instrument_factory
):
    """The brief's worked example: 25 (backtest) + 0 (drills) + 15 (15 of 30
    demo trades, gate too young to judge) + 0 (live).
    """
    _backtest(session)
    _trades(session, bot, paper_account, instrument_factory(), 15)

    result = engine.readiness(session, bot)

    assert result["percent"] == Decimal("40.0")
    incubation = _stage(result, "incubation")
    assert (incubation["progress"], incubation["factor"]) == (Decimal("0.5"), Decimal(1))
    assert incubation["detail"] == "15 / 30 demo trades"


def test_five_drills_add_15_points(session, bot, paper_account, instrument_factory):
    _backtest(session)
    _trades(session, bot, paper_account, instrument_factory(), 15)
    for key in engine.DRILL_KEYS:
        engine.set_drill(session, bot, key, True, None)

    result = engine.readiness(session, bot)

    assert result["percent"] == Decimal("55.0")
    assert _stage(result, "drills")["detail"] == "5 / 5 drills done"


def test_a_failing_gate_halves_a_finished_stage(session, bot, paper_account, instrument_factory):
    """30 demo trades is full progress, but an adherence of 0 fails the gate:
    30 × 0,5 = 15 for that stage.
    """
    _trades(session, bot, paper_account, instrument_factory(), 30, adherence=False)

    result = engine.readiness(session, bot)

    incubation = _stage(result, "incubation")
    assert (incubation["progress"], incubation["factor"]) == (Decimal(1), Decimal("0.5"))
    assert result["percent"] == Decimal("15.0")


def test_a_passing_gate_pays_the_full_stage(session, bot, paper_account, instrument_factory):
    _trades(session, bot, paper_account, instrument_factory(), 30)

    result = engine.readiness(session, bot)

    assert _stage(result, "incubation")["factor"] == Decimal(1)
    assert result["percent"] == Decimal("30.0")


def test_weights_and_targets_come_from_settings(session, bot, paper_account, instrument_factory):
    session.add(Setting(key="readiness_w_backtest", value="50"))
    session.add(Setting(key="readiness_demo_trades", value="15"))
    session.commit()
    _backtest(session)
    _trades(session, bot, paper_account, instrument_factory(), 15)

    result = engine.readiness(session, bot)

    # 50 for the backtest, 30 for the now-complete incubation target.
    assert result["percent"] == Decimal("80.0")


# --- stage results ----------------------------------------------------------


def test_demo_and_live_trades_land_in_their_own_column(
    session, bot, paper_account, account_factory, instrument_factory
):
    instrument = instrument_factory()
    live_account = account_factory(mode=Mode.LIVE)
    _trades(session, bot, paper_account, instrument, 4)
    _trades(session, bot, live_account, instrument, 2)

    results = engine.stage_results(session, bot)

    assert results["incubation"]["count"] == 4
    assert results["live"]["count"] == 2
    assert results["incubation"]["gate"]["mode"] == "paper"
    assert results["live"]["gate"]["mode"] == "live"
    assert results["incubation"]["expectancy_r"] == Decimal(1)


def test_another_bots_trades_are_not_counted(
    session, bot, bot_factory, paper_account, instrument_factory
):
    other, _token = bot_factory()
    _trades(session, other, paper_account, instrument_factory(), 5)

    assert engine.stage_results(session, bot)["incubation"]["count"] == 0


def test_the_bots_own_backtest_wins_over_a_strategy_wide_one(session, bot):
    _backtest(session, label="strategy wide")
    mine = _backtest(session, label="mine", bot_id=bot.id)

    assert engine.latest_passed_backtest(session, bot).id == mine.id


def test_a_failed_backtest_never_counts(session, bot):
    _backtest(session, passed=False, fail_reasons_json='["min_trades: 35 < 40"]')

    assert engine.latest_passed_backtest(session, bot) is None
    assert engine.readiness(session, bot)["percent"] == Decimal("0.0")


def test_a_result_for_another_strategy_never_counts(session, bot):
    _backtest(session, strategy="breakout")

    assert engine.latest_passed_backtest(session, bot) is None


# --- api --------------------------------------------------------------------


def test_strategy_endpoint_returns_the_three_columns(
    client, session, bot, paper_account, instrument_factory
):
    _backtest(session)
    _trades(session, bot, paper_account, instrument_factory(), 15)

    body = client.get(f"/api/bots/{bot.slug}/strategy").json()

    assert body["readiness"]["percent"] == "40.0"
    assert body["backtest"]["label"] == "donchian 4h 2020-2026"
    assert body["results"]["backtest"]["passed"] is True
    assert body["results"]["incubation"]["count"] == 15
    assert body["results"]["live"]["count"] == 0
    assert body["results"]["incubation"]["gate"]["passed"] is False
    assert [drill["key"] for drill in body["drills"]] == list(engine.DRILL_KEYS)
    assert all(drill["done"] is False for drill in body["drills"])


def test_strategy_endpoint_404s_for_an_unknown_bot(client):
    assert client.get("/api/bots/nope/strategy").status_code == 404


def test_put_drill_marks_it_done_and_is_idempotent(client, bot):
    first = client.put(
        f"/api/bots/{bot.slug}/drills/dry_run", json={"done": True, "note": "paper order"}
    )

    assert first.status_code == 200, first.text
    assert first.json()["done"] is True
    assert first.json()["done_ts"] is not None
    assert first.json()["note"] == "paper order"

    client.put(f"/api/bots/{bot.slug}/drills/dry_run", json={"done": True})
    body = client.get(f"/api/bots/{bot.slug}/strategy").json()
    done = [drill for drill in body["drills"] if drill["done"]]
    assert [drill["key"] for drill in done] == ["dry_run"]
    assert body["readiness"]["percent"] == "3.0"


def test_unchecking_a_drill_clears_its_timestamp(client, bot):
    client.put(f"/api/bots/{bot.slug}/drills/scheduling", json={"done": True})
    body = client.put(f"/api/bots/{bot.slug}/drills/scheduling", json={"done": False}).json()

    assert body["done"] is False
    assert body["done_ts"] is None


def test_an_unknown_drill_key_is_refused(client, bot):
    assert client.put(f"/api/bots/{bot.slug}/drills/coffee", json={"done": True}).status_code == 422


def test_fleet_readiness_lists_every_bot(client, session, bot, bot_factory):
    other, _token = bot_factory()
    _backtest(session)

    body = client.get("/api/bots/readiness/all").json()

    assert body == {bot.slug: "25.0", other.slug: "25.0"}
