from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from trade_ledger.bots import commands, supervisor
from trade_ledger.enums import AlertSeverity, CommandKind, EventKind
from trade_ledger.models import Alert, BotEvent, BotRun

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


class FakeProc:
    """Just enough of `Popen`: a pid and an exit code `poll` reports."""

    def __init__(self, code: int | None, pid: int = 4242):
        self.pid = pid
        self.code = code

    def poll(self) -> int | None:
        return self.code


class FakePopen:
    """Records how the child would have been started."""

    def __init__(self, code: int | None = None):
        self.code = code
        self.calls: list[dict] = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, **kwargs})
        return FakeProc(self.code)


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    """A DATA_DIR of its own per test, and no launches carried over."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    supervisor.reset()
    yield
    supervisor.reset()


def _env_file(slug: str, text: str = "BOT_TOKEN=t\n") -> Path:
    path = supervisor.bot_dir(slug) / ".env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _due_bot(bot_factory, **kwargs):
    """A bot whose slot passed an hour ago, with an env file."""
    bot, _ = bot_factory(schedule_every_s=3600, schedule_at="00:00", **kwargs)
    bot.created = NOW - timedelta(hours=3)
    bot.last_heartbeat = NOW - timedelta(hours=2)
    _env_file(bot.slug)
    return bot


def _alerts(session, kind=None) -> list[Alert]:
    rows = session.execute(select(Alert).order_by(Alert.id)).scalars().all()
    return [one for one in rows if kind is None or one.kind == kind]


def _events(session, bot) -> list[BotEvent]:
    rows = session.execute(select(BotEvent).order_by(BotEvent.id)).scalars().all()
    return [one for one in rows if one.bot_id == bot.id]


# --- plan -------------------------------------------------------------------


def test_plan_returns_bots_whose_slot_has_passed(session, bot_factory):
    bot = _due_bot(bot_factory)
    due = supervisor.plan(session, NOW)
    assert [one.id for one, _ in due] == [bot.id]
    assert due[0][1] <= NOW


def test_plan_skips_not_yet_due_disabled_and_remote(session, bot_factory):
    fresh = _due_bot(bot_factory)
    fresh.last_heartbeat = NOW  # next slot is an hour away
    off = _due_bot(bot_factory, enabled=False)
    remote = _due_bot(bot_factory, host="remote")
    ids = {one.id for one, _ in supervisor.plan(session, NOW)}
    assert ids == set()
    assert {fresh.id, off.id, remote.id} & ids == set()


def test_plan_does_not_launch_twice_in_one_slot(session, bot_factory):
    bot = _due_bot(bot_factory)
    popen = FakePopen(code=None)
    (_, slot) = supervisor.plan(session, NOW)[0]
    supervisor.launch(session, bot, now=NOW, slot=slot, popen=popen)

    # The process is still alive: nothing to plan.
    assert supervisor.plan(session, NOW) == []

    # It exits cleanly, and the same slot still must not fire again.
    popen_done = supervisor._LAUNCHES[bot.id]
    popen_done.popen.code = 0
    supervisor.reap(session, NOW)
    assert supervisor.plan(session, NOW) == []

    # The next slot does.
    later = NOW + timedelta(hours=1)
    assert [one.id for one, _ in supervisor.plan(session, later)] == [bot.id]


# --- launch -----------------------------------------------------------------


def test_launch_argv_cwd_and_event(session, bot_factory):
    bot = _due_bot(bot_factory)
    popen = FakePopen()
    started = supervisor.launch(session, bot, now=NOW, popen=popen)

    assert popen.calls[0]["argv"] == [
        sys.executable,
        "-m",
        "trade_ledger.botkit.run",
        "--bot",
        bot.slug,
        "--once",
    ]
    assert popen.calls[0]["cwd"] == str(supervisor.REPO_ROOT)
    # Its own process group, so a Ctrl-C to the app does not kill a bot mid-order.
    assert popen.calls[0]["start_new_session"] is True
    assert started.log_path.parent == supervisor.bot_dir(bot.slug) / "runs"
    assert started.log_path.exists()
    events = _events(session, bot)
    assert events[-1].kind == EventKind.INFO
    assert f"launched pid {started.pid}" in events[-1].message
    # The bot posts its own run; the supervisor writes none.
    assert session.execute(select(BotRun.id)).first() is None


def test_launch_dry_run_flag(session, bot_factory):
    bot = _due_bot(bot_factory, dry_run=True)
    popen = FakePopen()
    supervisor.launch(session, bot, now=NOW, popen=popen)
    assert popen.calls[0]["argv"][-1] == "--dry-run"

    # An explicit override wins over the bot's flag.
    supervisor.reset()
    supervisor.launch(session, bot, dry_run=False, now=NOW, popen=popen)
    assert popen.calls[1]["argv"][-1] == "--once"


def test_launch_env_comes_from_the_bot_file_not_the_app(session, bot_factory, monkeypatch):
    bot = _due_bot(bot_factory)
    popen = FakePopen()
    supervisor.launch(session, bot, now=NOW, popen=popen)
    env = popen.calls[0]["env"]
    assert env["BOT_TOKEN"] == "t"
    assert "EXCHANGE_KEY" not in env  # the app has no bot secrets to hand over
    assert env["TRADE_LEDGER_URL"].startswith("http://")
    assert "PATH" in env  # the process environment is still there

    _env_file(bot.slug, "BOT_TOKEN=t\nEXCHANGE_KEY=from-file\nTRADE_LEDGER_URL=http://elsewhere\n")
    supervisor.reset()
    supervisor.launch(session, bot, now=NOW, popen=popen)
    assert popen.calls[1]["env"]["EXCHANGE_KEY"] == "from-file"
    assert popen.calls[1]["env"]["TRADE_LEDGER_URL"] == "http://elsewhere"


def test_launch_without_env_file_alerts_and_skips(session, bot_factory):
    bot, _ = bot_factory()
    popen = FakePopen()
    assert supervisor.launch(session, bot, now=NOW, popen=popen) is None
    assert popen.calls == []
    alert = _alerts(session)[0]
    assert (alert.kind, alert.severity) == ("bot_env_missing", AlertSeverity.WARNING)


# --- reap -------------------------------------------------------------------


def _real_popen(argv, **kwargs):
    """Ignore the botkit argv (Task 8 owns it) and run a tiny failing script."""
    return subprocess.Popen(
        [sys.executable, "-c", "import sys; print('hello from the bot'); sys.exit(3)"], **kwargs
    )


def test_reap_records_a_real_non_zero_exit(session, bot_factory):
    bot = _due_bot(bot_factory)
    started = supervisor.launch(session, bot, now=NOW, popen=_real_popen)
    assert started.popen.wait(timeout=30) == 3

    supervisor.reap(session, NOW)

    assert "hello from the bot" in started.log_path.read_text()
    assert bot.id not in supervisor._LAUNCHES
    assert started.log_file is None  # the handle is closed
    error = [one for one in _events(session, bot) if one.kind == EventKind.ERROR]
    assert "exited 3" in error[0].message
    kinds = {one.kind for one in _alerts(session)}
    assert kinds == {"bot_exit", "bot_no_run"}
    assert all(one.severity == AlertSeverity.CRITICAL for one in _alerts(session))


def test_reap_of_a_clean_exit_with_a_run_is_quiet(session, bot_factory):
    bot = _due_bot(bot_factory)
    supervisor.launch(session, bot, now=NOW, popen=FakePopen(code=0))
    session.add(BotRun(bot_id=bot.id, started=NOW))
    session.flush()

    supervisor.reap(session, NOW)

    assert _alerts(session) == []
    assert _events(session, bot)[-1].message.endswith("exited 0")


def test_reap_leaves_a_running_process_alone(session, bot_factory):
    bot = _due_bot(bot_factory)
    supervisor.launch(session, bot, now=NOW, popen=FakePopen(code=None))
    supervisor.reap(session, NOW)
    assert bot.id in supervisor._LAUNCHES
    assert _alerts(session) == []


# --- restart cap ------------------------------------------------------------


def _crash(session, bot, now):
    supervisor.launch(session, bot, now=now, popen=FakePopen(code=1))
    supervisor.reap(session, now)


def test_fourth_launch_of_the_day_is_capped(session, bot_factory):
    bot = _due_bot(bot_factory)
    for minute in range(supervisor.RESTART_CAP):
        _crash(session, bot, NOW + timedelta(minutes=minute))

    popen = FakePopen()
    assert supervisor.launch(session, bot, now=NOW, popen=popen) is None
    assert popen.calls == []
    assert [one.kind for one in _alerts(session, "bot_restart_cap")] != []

    # The next UTC day starts over.
    assert supervisor.launch(session, bot, now=NOW + timedelta(days=1), popen=popen) is not None


# --- run_now hook -----------------------------------------------------------


def test_run_now_launches_through_the_hook(session, bot_factory, monkeypatch):
    bot = _due_bot(bot_factory)
    session.commit()
    seen: list = []
    monkeypatch.setattr(supervisor, "launch", lambda s, b, **kw: seen.append(b) or object())
    monkeypatch.setattr(commands, "LAUNCH_HOOKS", [])
    supervisor.register_hook()
    supervisor.register_hook()  # idempotent: the lifespan may run twice
    assert commands.LAUNCH_HOOKS == [supervisor.launch_hook]

    command = commands.issue(session, bot, CommandKind.RUN_NOW)

    assert [one.slug for one in seen] == [bot.slug]
    assert command.acked_ts is None  # a launched bot acks its own run_now


def test_run_now_under_the_cap_is_acked_failed(session, bot_factory, monkeypatch):
    bot = _due_bot(bot_factory)
    session.commit()
    for minute in range(supervisor.RESTART_CAP):
        _crash(session, bot, datetime.now(UTC) + timedelta(minutes=minute))

    monkeypatch.setattr(commands, "LAUNCH_HOOKS", [supervisor.launch_hook])
    command = commands.issue(session, bot, CommandKind.RUN_NOW)

    assert (command.result, command.result_detail) == ("failed", "restart cap")


def test_a_broken_launch_fails_the_command_instead_of_raising(session, bot_factory, monkeypatch):
    bot = _due_bot(bot_factory)
    session.commit()

    def boom(*args, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(supervisor, "launch", boom)
    monkeypatch.setattr(commands, "LAUNCH_HOOKS", [supervisor.launch_hook])

    command = commands.issue(session, bot, CommandKind.RUN_NOW)

    assert command.result == "failed"
    assert "no such file" in command.result_detail


# --- the loop and the lifespan ----------------------------------------------


def test_loop_plans_launches_then_reaps(session, bot_factory, monkeypatch):
    bot = _due_bot(bot_factory)
    session.commit()  # the loop opens a session of its own
    calls: list = []
    monkeypatch.setattr(supervisor, "launch", lambda s, b, **kw: calls.append(("launch", b.slug)))
    monkeypatch.setattr(supervisor, "reap", lambda s: calls.append(("reap", None)))

    async def stop(_):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(supervisor.loop(0, sleep=stop))

    assert calls == [("launch", bot.slug), ("reap", None)]


def test_lifespan_starts_the_supervisor_and_registers_the_hook(monkeypatch):
    from fastapi.testclient import TestClient

    from trade_ledger.main import create_app

    seen: list[str] = []

    async def fake_loop(*args, **kwargs):
        seen.append("started")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            seen.append("cancelled")
            raise

    monkeypatch.setattr(supervisor, "loop", fake_loop)
    monkeypatch.setattr(commands, "LAUNCH_HOOKS", [])
    with TestClient(create_app(db_path=":memory:", background=True)):
        assert seen == ["started"]
        assert commands.LAUNCH_HOOKS == [supervisor.launch_hook]

    assert seen == ["started", "cancelled"]
    assert commands.LAUNCH_HOOKS == []


# --- one child at a time ----------------------------------------------------


def test_launch_refuses_while_the_previous_child_is_alive(session, bot_factory):
    bot = _due_bot(bot_factory)
    popen = FakePopen(code=None)
    first = supervisor.launch(session, bot, now=NOW, popen=popen)

    assert supervisor.launch(session, bot, now=NOW, popen=popen) is None
    assert len(popen.calls) == 1
    assert supervisor._LAUNCHES[bot.id] is first  # not overwritten, not orphaned
    assert "still running" in _events(session, bot)[-1].message


def test_run_now_while_running_is_acked_failed(session, bot_factory, monkeypatch):
    bot = _due_bot(bot_factory)
    supervisor.launch(session, bot, now=NOW, popen=FakePopen(code=None))
    session.commit()

    monkeypatch.setattr(commands, "LAUNCH_HOOKS", [supervisor.launch_hook])
    command = commands.issue(session, bot, CommandKind.RUN_NOW)

    assert (command.result, command.result_detail) == ("failed", "already running")


def test_launch_reaps_a_child_that_exited_between_ticks(session, bot_factory):
    bot = _due_bot(bot_factory)
    supervisor.launch(session, bot, now=NOW, popen=FakePopen(code=1))

    # No reap ran in between: the next launch must collect the corpse first,
    # so the failure is alerted and counted rather than silently dropped.
    assert supervisor.launch(session, bot, now=NOW, popen=FakePopen(code=None)) is not None
    assert [one.kind for one in _alerts(session, "bot_exit")] == ["bot_exit"]
    assert supervisor._FAILURES[bot.id] == (NOW.date(), 1)


def test_a_spawn_that_fails_alerts_and_does_not_record_a_launch(session, bot_factory):
    bot = _due_bot(bot_factory)

    def boom(argv, **kwargs):
        raise OSError("Exec format error")

    with pytest.raises(OSError, match="Exec format error"):
        supervisor.launch(session, bot, now=NOW, popen=boom)

    assert bot.id not in supervisor._LAUNCHES
    assert [one.kind for one in _alerts(session)] == ["bot_launch_failed"]
