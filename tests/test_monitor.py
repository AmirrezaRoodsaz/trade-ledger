from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from trade_ledger import db
from trade_ledger.bots import alerts, monitor, supervisor, telegram
from trade_ledger.enums import (
    AlertSeverity,
    BotStatus,
    CommandKind,
    EventKind,
    RunStatus,
    TradeStatus,
)
from trade_ledger.models import Alert, BotCommand, BotEvent, BotRun, BotState, Trade

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


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


def _alerts(session, bot=None) -> list[Alert]:
    rows = session.execute(select(Alert).order_by(Alert.id)).scalars().all()
    return [one for one in rows if bot is None or one.bot_id == bot.id]


def _commands(session, bot, kind) -> list[BotCommand]:
    return [
        one
        for one in session.execute(select(BotCommand).order_by(BotCommand.id)).scalars().all()
        if one.bot_id == bot.id and one.kind == kind
    ]


def _healthy(bot) -> None:
    """A bot that no rule has anything to say about."""
    bot.last_heartbeat = NOW


# --- alerts and dedupe ------------------------------------------------------


def test_raise_alert_creates_one_row(session, bot_factory):
    bot, _ = bot_factory()

    alert = alerts.raise_alert(session, bot, AlertSeverity.CRITICAL, "kill_k1", "down", now=NOW)

    assert alert is not None
    assert alert.severity == AlertSeverity.CRITICAL
    assert alert.acknowledged is False


def test_raise_alert_dedupes_the_same_kind_within_six_hours(session, bot_factory):
    bot, _ = bot_factory()
    alerts.raise_alert(session, bot, AlertSeverity.CRITICAL, "kill_k1", "down", now=NOW)

    again = alerts.raise_alert(
        session, bot, AlertSeverity.CRITICAL, "kill_k1", "still down", now=NOW + timedelta(hours=5)
    )

    assert again is None
    assert len(_alerts(session, bot)) == 1


def test_raise_alert_fires_again_after_the_cooldown(session, bot_factory):
    bot, _ = bot_factory()
    alerts.raise_alert(session, bot, AlertSeverity.CRITICAL, "kill_k1", "down", now=NOW)

    again = alerts.raise_alert(
        session,
        bot,
        AlertSeverity.CRITICAL,
        "kill_k1",
        "down again",
        now=NOW + timedelta(hours=6, seconds=1),
    )

    assert again is not None
    assert len(_alerts(session, bot)) == 2


def test_an_acknowledged_alert_still_holds_the_cooldown(session, bot_factory):
    bot, _ = bot_factory()
    first = alerts.raise_alert(session, bot, AlertSeverity.WARNING, "kill_k2", "weak", now=NOW)
    first.acknowledged = True

    assert (
        alerts.raise_alert(
            session, bot, AlertSeverity.WARNING, "kill_k2", "weak", now=NOW + timedelta(hours=1)
        )
        is None
    )


def test_the_cooldown_is_per_bot_and_per_kind(session, bot_factory):
    bot, _ = bot_factory()
    other, _ = bot_factory()
    alerts.raise_alert(session, bot, AlertSeverity.CRITICAL, "kill_k1", "down", now=NOW)

    assert alerts.raise_alert(session, other, AlertSeverity.CRITICAL, "kill_k1", "x", now=NOW)
    assert alerts.raise_alert(session, bot, AlertSeverity.CRITICAL, "kill_k5", "x", now=NOW)


def test_notifiers_are_called_and_a_broken_one_does_not_lose_the_alert(session, bot_factory):
    bot, _ = bot_factory()
    seen = []
    alerts.NOTIFIERS.append(lambda alert, bot: seen.append(alert.kind))
    alerts.NOTIFIERS.append(lambda alert, bot: 1 / 0)
    try:
        alert = alerts.raise_alert(session, bot, AlertSeverity.INFO, "test", "hi", now=NOW)
    finally:
        del alerts.NOTIFIERS[-2:]

    assert seen == ["test"]
    assert alert is not None


# --- monitor.evaluate -------------------------------------------------------


def test_evaluate_stores_the_status_and_stays_quiet_on_a_healthy_bot(session, bot_factory):
    bot, _ = bot_factory()
    _healthy(bot)
    _state(session, bot, equity_eur=Decimal(2500), peak_equity_eur=Decimal(2500))

    report = monitor.evaluate(session, bot, NOW)

    assert report["status"] == BotStatus.OK
    assert bot.status == BotStatus.OK
    assert _alerts(session, bot) == []


def test_k1_raises_a_critical_alert_and_queues_a_pause(session, bot_factory):
    bot, _ = bot_factory()
    _healthy(bot)
    _state(session, bot, equity_eur=Decimal(1900), peak_equity_eur=Decimal(2000))

    monitor.evaluate(session, bot, NOW)

    alert = next(one for one in _alerts(session, bot) if one.kind == "kill_k1")
    assert alert.severity == AlertSeverity.CRITICAL
    pauses = _commands(session, bot, CommandKind.PAUSE)
    assert len(pauses) == 1
    assert pauses[0].issued_by == "system"


def test_a_second_evaluate_within_six_hours_adds_no_alert_and_no_command(session, bot_factory):
    bot, _ = bot_factory()
    _healthy(bot)
    _state(session, bot, equity_eur=Decimal(1900), peak_equity_eur=Decimal(2000))
    monitor.evaluate(session, bot, NOW)

    bot.last_heartbeat = NOW + timedelta(hours=1)
    monitor.evaluate(session, bot, NOW + timedelta(hours=1))

    assert len([one for one in _alerts(session, bot) if one.kind == "kill_k1"]) == 1
    assert len(_commands(session, bot, CommandKind.PAUSE)) == 1


def test_a_position_without_a_stop_queues_a_flat(session, bot_factory):
    bot, _ = bot_factory()
    _healthy(bot)
    _state(session, bot, positions=[{"symbol": "BTC/USDT:USDT", "stop_present": False}])

    report = monitor.evaluate(session, bot, NOW)

    assert report["state"]["positions_without_stop"] == 1
    flats = _commands(session, bot, CommandKind.FLAT)
    assert len(flats) == 1
    assert flats[0].issued_by == "system"
    assert next(one for one in _alerts(session, bot) if one.kind == "kill_k4").severity == (
        AlertSeverity.CRITICAL
    )


def test_a_stale_bot_is_alerted_but_not_asked_to_go_flat(session, bot_factory):
    """No point queueing `flat` for a process that is not listening."""
    bot, _ = bot_factory(schedule_every_s=14400, grace_s=3300)
    bot.last_heartbeat = NOW - timedelta(hours=5)
    _state(session, bot, positions=[{"symbol": "BTC/USDT:USDT", "stop_present": False}])

    report = monitor.evaluate(session, bot, NOW)

    assert report["status"] == BotStatus.STALE
    assert bot.status == BotStatus.STALE
    assert _commands(session, bot, CommandKind.FLAT) == []
    # kill_k1 too: this state has positions but no equity, so K1 warns that it
    # cannot evaluate at all.
    assert {one.kind for one in _alerts(session, bot)} == {"kill_k1", "kill_k4", "kill_k5"}


def test_a_k1_that_cannot_evaluate_warns_rather_than_going_critical(session, bot_factory):
    """K1 is inert on an account whose quote currency is not EUR: the bot
    reports no `equity_eur`, so the capital brake can never fire. Silence there
    reads as "fine", which is exactly wrong."""
    bot, _ = bot_factory()
    _healthy(bot)
    _state(session, bot, positions=[{"symbol": "BTC/USDT:USDT", "stop_present": True}])

    report = monitor.evaluate(session, bot, NOW)

    k1 = report["kill_rules"][0]
    assert (k1["rule"], k1["status"], k1["action"]) == ("K1", "warning", "alert")
    assert "no equity in EUR reported" in k1["detail"]
    # A warning, not a critical: K1 tripping and K1 not working are different
    # news. And no `pause` — there is nothing measured to pause over.
    alert = next(one for one in _alerts(session, bot) if one.kind == "kill_k1")
    assert alert.severity == AlertSeverity.WARNING
    assert _commands(session, bot, CommandKind.PAUSE) == []

    # Deduped like every other rule: a second evaluation adds nothing.
    monitor.evaluate(session, bot, NOW + timedelta(minutes=1))
    assert len([one for one in _alerts(session, bot) if one.kind == "kill_k1"]) == 1


def test_a_paused_bot_is_not_asked_to_pause_again(session, bot_factory):
    bot, _ = bot_factory(paused_entries=True)
    _healthy(bot)
    _state(session, bot, equity_eur=Decimal(1900), peak_equity_eur=Decimal(2000))

    monitor.evaluate(session, bot, NOW)

    assert _commands(session, bot, CommandKind.PAUSE) == []


def test_a_k2_warning_alerts_at_warning_severity(
    session, bot_factory, account_factory, instrument_factory
):
    """20 closed trades at 10 € risk: 40 R won against 50 R lost, PF 0,8."""
    account = account_factory()
    bot, _ = bot_factory(account_id=account.id)
    instrument = instrument_factory()
    _healthy(bot)
    results = [Decimal(100)] * 4 + [Decimal(-50)] * 10 + [Decimal(0)] * 6
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
                closed_ts=NOW - timedelta(hours=len(results) - index),
            )
        )
    session.flush()

    monitor.evaluate(session, bot, NOW)

    alert = next(one for one in _alerts(session, bot) if one.kind == "kill_k2")
    assert alert.severity == AlertSeverity.WARNING
    assert _commands(session, bot, CommandKind.PAUSE) == []


def test_a_disabled_bot_gets_no_alerts_and_no_commands(session, bot_factory):
    """Switched off is not misbehaving. The status still updates."""
    bot, _ = bot_factory(enabled=False, schedule_every_s=14400, grace_s=3300)
    bot.last_heartbeat = NOW - timedelta(days=2)
    _state(session, bot, positions=[{"symbol": "BTC/USDT:USDT", "stop_present": False}])

    report = monitor.evaluate(session, bot, NOW)

    assert report["status"] == BotStatus.DISABLED
    assert bot.status == BotStatus.DISABLED
    assert _alerts(session, bot) == []
    assert session.execute(select(BotCommand)).scalars().all() == []
    assert [one["status"] for one in report["kill_rules"] if one["rule"] == "K5"] == ["triggered"]


def test_a_queued_command_goes_through_commands_issue(session, bot_factory):
    bot, _ = bot_factory()
    _healthy(bot)
    _state(session, bot, equity_eur=Decimal(1900), peak_equity_eur=Decimal(2000))

    monitor.evaluate(session, bot, NOW)

    pause = _commands(session, bot, CommandKind.PAUSE)[0]
    assert pause.reason == "K1 capital brake"
    assert pause.issued_by == "system"
    assert pause.issued_ts == NOW


def test_a_flat_is_queued_despite_the_confirm_guard(session, bot_factory):
    """`commands.issue` refuses an unconfirmed flat; the monitor knows the slug."""
    bot, _ = bot_factory()
    _healthy(bot)
    _state(session, bot, positions=[{"symbol": "BTC/USDT:USDT", "stop_present": False}])

    monitor.evaluate(session, bot, NOW)

    flat = _commands(session, bot, CommandKind.FLAT)[0]
    assert flat.reason == "K4 naked position"
    assert flat.issued_by == "system"


def test_a_pause_acked_as_failed_is_not_re_issued_within_the_cooldown(session, bot_factory):
    """Nothing is pending any more, so only the cooldown stops the monitor
    from queueing a fresh pause on every 60-second tick.
    """
    bot, _ = bot_factory()
    _healthy(bot)
    _state(session, bot, equity_eur=Decimal(1900), peak_equity_eur=Decimal(2000))
    monitor.evaluate(session, bot, NOW)
    pause = _commands(session, bot, CommandKind.PAUSE)[0]
    pause.acked_ts = NOW + timedelta(minutes=1)
    pause.result = "error"
    session.commit()

    bot.last_heartbeat = NOW + timedelta(minutes=2)
    monitor.evaluate(session, bot, NOW + timedelta(minutes=2))

    assert len(_commands(session, bot, CommandKind.PAUSE)) == 1


def test_the_same_command_is_queued_again_after_the_cooldown(session, bot_factory):
    bot, _ = bot_factory()
    _healthy(bot)
    _state(session, bot, equity_eur=Decimal(1900), peak_equity_eur=Decimal(2000))
    monitor.evaluate(session, bot, NOW)
    pause = _commands(session, bot, CommandKind.PAUSE)[0]
    pause.acked_ts = NOW
    pause.result = "error"
    session.commit()

    later = NOW + timedelta(hours=6, minutes=1)
    bot.last_heartbeat = later
    monitor.evaluate(session, bot, later)

    assert len(_commands(session, bot, CommandKind.PAUSE)) == 2


def test_a_firing_rule_writes_one_kill_rule_event(session, bot_factory):
    bot, _ = bot_factory()
    _healthy(bot)
    _state(session, bot, equity_eur=Decimal(1900), peak_equity_eur=Decimal(2000))

    monitor.evaluate(session, bot, NOW)
    bot.last_heartbeat = NOW + timedelta(hours=1)
    monitor.evaluate(session, bot, NOW + timedelta(hours=1))

    events = [
        one
        for one in session.execute(select(BotEvent)).scalars().all()
        if one.bot_id == bot.id and one.kind == EventKind.KILL_RULE
    ]
    assert len(events) == 1, "deduped alerts must not repeat the timeline entry"
    assert json.loads(events[0].payload_json)["rule"] == "K1"
    assert events[0].message.startswith("K1 triggered:")


def test_health_writes_nothing(session, bot_factory):
    bot, _ = bot_factory()
    _healthy(bot)
    _state(session, bot, equity_eur=Decimal(1900), peak_equity_eur=Decimal(2000))

    report = monitor.health(session, bot, NOW)

    assert report["kill_rules"][0]["status"] == "triggered"
    assert _alerts(session, bot) == []
    assert _commands(session, bot, CommandKind.PAUSE) == []
    assert bot.status == "ok"


def test_evaluate_all_covers_every_bot(session, bot_factory):
    _first, _ = bot_factory(enabled=False)
    second, _ = bot_factory(schedule_every_s=14400, grace_s=3300)
    second.last_heartbeat = NOW - timedelta(hours=5)

    reports = monitor.evaluate_all(session, NOW)

    assert [one["status"] for one in reports] == [BotStatus.DISABLED, BotStatus.STALE]


# --- the loop ---------------------------------------------------------------


def test_the_loop_runs_one_iteration_then_stops(session, bot_factory, monkeypatch):
    """A fake sleep that cancels ends the loop after exactly one pass."""
    bot, _ = bot_factory(schedule_every_s=14400, grace_s=3300)
    bot.last_heartbeat = NOW - timedelta(days=1)
    session.commit()
    calls = []

    async def fake_sleep(seconds):
        calls.append(seconds)
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(monitor.loop(30, sleep=fake_sleep))

    assert calls == [30]
    with db.SessionLocal() as fresh:
        assert fresh.get(type(bot), bot.id).status == BotStatus.STALE
        assert fresh.execute(select(Alert)).scalars().all() != []


def test_the_loop_survives_a_failing_iteration(monkeypatch):
    calls = []

    def boom(session, now=None):
        raise RuntimeError("db gone")

    monkeypatch.setattr(monitor, "evaluate_all", boom)

    async def fake_sleep(seconds):
        calls.append(seconds)
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(monitor.loop(5, sleep=fake_sleep))

    assert calls == [5]


# --- API --------------------------------------------------------------------


def test_health_endpoint_shape(client, session, bot_factory):
    bot, _ = bot_factory(schedule_every_s=14400, schedule_at="00:05", grace_s=3300)
    bot.last_heartbeat = NOW
    _state(session, bot, equity_eur=Decimal(2000), peak_equity_eur=Decimal(2500))
    session.commit()

    body = client.get(f"/api/bots/{bot.slug}/health").json()

    assert set(body) == {
        "status",
        "kill_rules",
        "last_heartbeat",
        "next_run",
        "deadline",
        "last_run",
        "state",
        "stage_capital_eur",
    }
    assert [one["rule"] for one in body["kill_rules"]] == ["K1", "K2", "K3", "K4", "K5"]
    assert body["state"]["equity_eur"] == "2000"
    assert body["state"]["drawdown_pct"] == "20.00"
    assert body["stage_capital_eur"] == "2500"


def test_bot_list_carries_status_and_the_kill_summary(client, session, bot_factory):
    bot, _ = bot_factory()
    bot.last_heartbeat = NOW
    _state(
        session,
        bot,
        equity_eur=Decimal(1900),
        peak_equity_eur=Decimal(2000),
        positions=[{"symbol": "BTC/USDT:USDT", "stop_present": False}],
    )
    session.commit()

    item = client.get("/api/bots").json()[0]

    assert item["equity_eur"] == "1900"
    assert item["drawdown_pct"] == "5.00"
    assert item["positions_count"] == 1
    assert item["positions_without_stop"] == 1
    assert item["kill_summary"]["K1"] == "triggered"
    assert item["kill_summary"]["K4"] == "triggered"
    assert item["next_run"] is not None


def test_runs_events_and_log_endpoints(client, session, bot_factory, tmp_path):
    bot, _ = bot_factory()
    log = tmp_path / "7.log"
    log.write_text("hello from the bot\n")
    run = BotRun(
        bot_id=bot.id,
        started=NOW,
        finished=NOW,
        status=RunStatus.OK,
        summary_json=json.dumps({"orders": 1}),
        log_path=str(log),
    )
    session.add(run)
    session.add(BotEvent(bot_id=bot.id, ts=NOW, kind=EventKind.HEARTBEAT, message="beat"))
    session.add(BotEvent(bot_id=bot.id, ts=NOW, kind=EventKind.ORDER, message="filled"))
    session.commit()

    runs = client.get(f"/api/bots/{bot.slug}/runs").json()
    assert runs[0]["summary"] == {"orders": 1}
    assert runs[0]["has_log"] is True

    text = client.get(f"/api/bots/{bot.slug}/runs/{run.id}/log")
    assert text.text == "hello from the bot\n"

    events = client.get(f"/api/bots/{bot.slug}/events", params={"kind": "order"}).json()
    assert [one["message"] for one in events] == ["filled"]
    assert len(client.get(f"/api/bots/{bot.slug}/events").json()) == 2


def test_a_run_without_a_log_is_404(client, session, bot_factory):
    bot, _ = bot_factory()
    run = BotRun(bot_id=bot.id, started=NOW, status=RunStatus.OK)
    session.add(run)
    session.commit()

    assert client.get(f"/api/bots/{bot.slug}/runs/{run.id}/log").status_code == 404


def test_a_foreign_runs_log_is_404(client, session, bot_factory):
    bot, _ = bot_factory()
    other, _ = bot_factory()
    run = BotRun(bot_id=other.id, started=NOW, status=RunStatus.OK)
    session.add(run)
    session.commit()

    assert client.get(f"/api/bots/{bot.slug}/runs/{run.id}/log").status_code == 404


def test_alerts_endpoints_list_filter_and_ack(client, session, bot_factory):
    bot, _ = bot_factory()
    other, _ = bot_factory()
    alerts.raise_alert(session, bot, AlertSeverity.CRITICAL, "kill_k1", "down", now=NOW)
    alerts.raise_alert(session, other, AlertSeverity.WARNING, "kill_k2", "weak", now=NOW)
    session.commit()

    mine = client.get("/api/alerts", params={"bot": bot.slug}).json()
    assert [one["kind"] for one in mine] == ["kill_k1"]

    acked = client.post(f"/api/alerts/{mine[0]['id']}/ack").json()
    assert acked["acknowledged"] is True

    unacked = client.get("/api/alerts", params={"unacked": True}).json()
    assert [one["kind"] for one in unacked] == ["kill_k2"]
    assert len(client.get("/api/alerts").json()) == 2


def test_acking_an_unknown_alert_is_404(client):
    assert client.post("/api/alerts/999/ack").status_code == 404


# --- app wiring -------------------------------------------------------------


def _idle_loop(monkeypatch, module) -> list[str]:
    """Replace `module.loop` with one that records start and cancellation.

    Every background loop the lifespan starts has to be replaced, not just the
    one a test is about: a real supervisor loop against the test's database is
    a second writer nobody asked for.
    """
    seen: list[str] = []

    async def fake_loop(*args, **kwargs):
        seen.append("started")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            seen.append("cancelled")
            raise

    monkeypatch.setattr(module, "loop", fake_loop)
    return seen


def _spy_loop(monkeypatch) -> list[str]:
    """The monitor loop, with the supervisor's silenced alongside it."""
    _idle_loop(monkeypatch, supervisor)
    return _idle_loop(monkeypatch, monitor)


def test_create_app_starts_no_background_task_by_default(monkeypatch):
    from fastapi.testclient import TestClient

    from trade_ledger.main import create_app

    seen = _spy_loop(monkeypatch)
    with TestClient(create_app(db_path=":memory:")) as test_client:
        assert test_client.get("/api/bots").status_code == 200

    assert seen == []


def test_create_app_with_background_runs_and_cancels_the_monitor(monkeypatch):
    from fastapi.testclient import TestClient

    from trade_ledger.main import create_app

    seen = _spy_loop(monkeypatch)
    with TestClient(create_app(db_path=":memory:", background=True)) as test_client:
        assert test_client.get("/api/bots").status_code == 200
        assert seen == ["started"]

    assert seen == ["started", "cancelled"]


def _spy_telegram_loop(monkeypatch) -> list[str]:
    """Same shape as `_spy_loop`, for `telegram.loop`."""
    return _idle_loop(monkeypatch, telegram)


def test_background_starts_telegram_and_registers_notify_when_configured(monkeypatch):
    """Registration must happen only once an app actually goes live with
    `background=True` — never at import, or a developer with real Telegram
    keys in `.env` would arm a live notifier on every `pytest` run.
    """
    from fastapi.testclient import TestClient

    from trade_ledger.main import create_app

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    _spy_loop(monkeypatch)
    telegram_seen = _spy_telegram_loop(monkeypatch)

    try:
        with TestClient(create_app(db_path=":memory:", background=True)) as test_client:
            assert test_client.get("/api/bots").status_code == 200
            assert telegram_seen == ["started"]
            assert telegram.notify in alerts.NOTIFIERS

        assert telegram_seen == ["started", "cancelled"]
    finally:
        if telegram.notify in alerts.NOTIFIERS:
            alerts.NOTIFIERS.remove(telegram.notify)


def test_background_skips_telegram_when_unconfigured(monkeypatch):
    from fastapi.testclient import TestClient

    from trade_ledger.main import create_app

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    _spy_loop(monkeypatch)
    telegram_seen = _spy_telegram_loop(monkeypatch)

    with TestClient(create_app(db_path=":memory:", background=True)) as test_client:
        assert test_client.get("/api/bots").status_code == 200
        assert telegram_seen == []
        assert telegram.notify not in alerts.NOTIFIERS
