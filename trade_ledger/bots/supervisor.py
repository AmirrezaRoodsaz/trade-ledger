"""Local supervisor: it starts the bots that live on this machine.

The app owns *when* a local bot runs, the bot owns everything after that.
So this module is deliberately thin: work out which bots are due, start
`python -m trade_ledger.botkit.run`, keep the stdout, and notice when the
process dies. It never imports the botkit — the bot is a subprocess, not a
library call, which is what keeps the exchange wrapper out of the app.

Runs are *not* created here. A bot posts its own `BotRun` when it starts
(the push protocol), so the supervisor's own bookkeeping — which pid it
started, when, and where the log went — stays in memory, in `_LAUNCHES`.
A launch that never turns into a run is exactly the failure that alerts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from .. import db
from ..enums import AlertSeverity, BotHost, CommandKind, EventKind
from ..models import Bot, BotCommand, BotEvent, BotRun
from ..settings import env_values, get_settings
from . import commands
from .alerts import raise_alert
from .schedule import next_run

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Three crashed launches in a UTC day and the supervisor stops trying. A bot
# that dies on start dies the same way at 04:05, 08:05 and 12:05; the fourth
# attempt tells the operator nothing the alert has not already said.
RESTART_CAP = 3


@dataclass
class Launch:
    """One live child process. In memory only — it dies with the app."""

    bot_id: int
    pid: int
    popen: Any
    started: datetime
    log_path: Path
    log_file: Any = None

    def close(self) -> None:
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None


# ponytail: module-level dicts rather than a Supervisor object — there is one
# supervisor per process, and a class would only be a namespace with a
# singleton. `reset()` is how a test (or a restart) clears them.
_LAUNCHES: dict[int, Launch] = {}
_LAST_SLOT: dict[int, datetime] = {}
_FAILURES: dict[int, tuple[date, int]] = {}


def reset() -> None:
    """Forget every launch, slot and failure count. For tests and restarts."""
    _LAUNCHES.clear()
    _LAST_SLOT.clear()
    _FAILURES.clear()


def bot_dir(slug: str) -> Path:
    return Path(get_settings().DATA_DIR) / "bots" / slug


def _event(session: Session, bot: Bot, kind: EventKind, message: str, now: datetime) -> None:
    session.add(BotEvent(bot_id=bot.id, ts=now, kind=kind, message=message))


def _over_cap(bot_id: int, now: datetime) -> bool:
    day, count = _FAILURES.get(bot_id, (None, 0))
    return day == now.date() and count >= RESTART_CAP


def _count_failure(bot_id: int, now: datetime) -> None:
    day, count = _FAILURES.get(bot_id, (None, 0))
    _FAILURES[bot_id] = (now.date(), count + 1 if day == now.date() else 1)


def _has_run_since(session: Session, bot: Bot, since: datetime) -> bool:
    return (
        session.execute(
            select(BotRun.id).where(BotRun.bot_id == bot.id, BotRun.started >= since).limit(1)
        ).first()
        is not None
    )


def plan(session: Session, now: datetime | None = None) -> list[tuple[Bot, datetime]]:
    """The enabled local bots whose slot has passed, with that slot.

    The slot is counted from the last heartbeat (or from creation for a bot
    that has never reported), so a bot that just ran is not due again until
    a whole interval later. A bot with a live process, or one already
    launched for this slot, is not due at all — `_LAST_SLOT` is what stops a
    30-second loop from launching the same bot sixty times an hour.
    """
    now = now or datetime.now(UTC)
    due: list[tuple[Bot, datetime]] = []
    rows = session.execute(
        select(Bot).where(Bot.enabled.is_(True), Bot.host == BotHost.LOCAL).order_by(Bot.id)
    ).scalars()
    for bot in rows:
        if bot.id in _LAUNCHES:
            continue
        slot = next_run(bot.schedule_every_s, bot.schedule_at, bot.last_heartbeat or bot.created)
        if slot > now:
            continue
        # The bot is overdue. Which slot for? A crashed bot never heartbeats,
        # so its reference slot would stay the same one forever and a single
        # failed launch would retire it; the current slot gives it one attempt
        # per schedule tick instead, which the restart cap then bounds.
        current = next_run(bot.schedule_every_s, bot.schedule_at, now) - timedelta(
            seconds=bot.schedule_every_s
        )
        slot = max(slot, current)
        last = _LAST_SLOT.get(bot.id)
        if last is not None and last >= slot:
            continue
        due.append((bot, slot))
    return due


def launch(
    session: Session,
    bot: Bot,
    dry_run: bool | None = None,
    *,
    now: datetime | None = None,
    slot: datetime | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
) -> Launch | None:
    """Start the bot, or return `None` and alert if it must not be started.

    The child's environment is this process's environment plus the bot's own
    `DATA_DIR/bots/<slug>/.env`. The app has no exchange keys and does not go
    looking for any: whatever the bot needs to trade, including its own
    `BOT_TOKEN`, is in that file or the bot does not get it.
    """
    now = now or datetime.now(UTC)
    if _over_cap(bot.id, now):
        raise_alert(
            session,
            bot,
            AlertSeverity.CRITICAL,
            "bot_restart_cap",
            f"restart cap reached: {RESTART_CAP} failed launches today, skipping until tomorrow",
            now=now,
        )
        session.commit()
        return None

    env_file = bot_dir(bot.slug) / ".env"
    if not env_file.exists():
        raise_alert(
            session,
            bot,
            AlertSeverity.WARNING,
            "bot_env_missing",
            f"no env file at {env_file}",
            now=now,
        )
        session.commit()
        return None

    settings = get_settings()
    env = {**os.environ, **env_values(env_file)}
    env.setdefault("TRADE_LEDGER_URL", f"http://{settings.HOST}:{settings.PORT}")

    argv = [sys.executable, "-m", "trade_ledger.botkit.run", "--bot", bot.slug, "--once"]
    if bot.dry_run if dry_run is None else dry_run:
        argv.append("--dry-run")

    log_path = bot_dir(bot.slug) / "runs" / f"supervisor-{now.strftime('%Y%m%dT%H%M%S')}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("ab")
    try:
        child = popen(argv, cwd=str(REPO_ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT)
    except Exception:
        handle.close()
        raise

    started = Launch(
        bot_id=bot.id, pid=child.pid, popen=child, started=now, log_path=log_path, log_file=handle
    )
    _LAUNCHES[bot.id] = started
    _LAST_SLOT[bot.id] = slot or now
    _event(session, bot, EventKind.INFO, f"launched pid {child.pid}, log {log_path.name}", now)
    session.commit()
    return started


def reap(session: Session, now: datetime | None = None) -> None:
    """Collect finished children: event, alert on failure, count the failure."""
    now = now or datetime.now(UTC)
    for bot_id, started in list(_LAUNCHES.items()):
        code = started.popen.poll()
        if code is None:
            continue
        del _LAUNCHES[bot_id]
        started.close()
        bot = session.get(Bot, bot_id)
        if bot is None:  # the bot was deleted while its process ran
            continue
        if code == 0:
            _event(session, bot, EventKind.INFO, f"pid {started.pid} exited 0", now)
        else:
            _event(session, bot, EventKind.ERROR, f"pid {started.pid} exited {code}", now)
            raise_alert(
                session,
                bot,
                AlertSeverity.CRITICAL,
                "bot_exit",
                f"{bot.slug} exited with code {code}; see {started.log_path}",
                now=now,
            )
            _count_failure(bot_id, now)
        # ponytail: checked on a clean exit too. A bot that returns 0 without
        # ever posting a run did nothing at all, which is the quieter and so
        # the more dangerous version of the same failure.
        if not _has_run_since(session, bot, started.started):
            raise_alert(
                session,
                bot,
                AlertSeverity.CRITICAL,
                "bot_no_run",
                f"{bot.slug} exited before starting a run; see {started.log_path}",
                now=now,
            )
        session.commit()


def _fail_command(session: Session, bot: Bot, detail: str) -> None:
    """Ack the `run_now` that triggered a launch we could not perform.

    The hook is handed a bot, not the command, so the command is the newest
    unacked `run_now` for that bot — `issue` has just committed it.
    """
    command = (
        session.execute(
            select(BotCommand)
            .where(
                BotCommand.bot_id == bot.id,
                BotCommand.kind == CommandKind.RUN_NOW,
                BotCommand.acked_ts.is_(None),
            )
            .order_by(BotCommand.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if command is not None:
        commands.ack(session, command, "failed", detail)


def launch_hook(bot: Bot) -> None:
    """`commands.LAUNCH_HOOKS` entry: a `run_now` on a local bot starts now.

    Nothing here may raise: `issue` has already committed the command, and a
    supervisor problem must show up as a failed command rather than as a 500
    on the button the operator pressed.
    """
    session = object_session(bot)
    if session is None:  # pragma: no cover - a detached bot has no caller session
        return
    now = datetime.now(UTC)
    try:
        if launch(session, bot, now=now) is not None:
            return
        detail = "restart cap" if _over_cap(bot.id, now) else "no env file"
    except Exception as exc:
        log.exception("run_now launch failed for %s", bot.slug)
        detail = f"{type(exc).__name__}: {exc}"
    try:
        _fail_command(session, bot, detail)
    except Exception:  # the command is cosmetic next to not raising here
        log.exception("could not ack the failed run_now for %s", bot.slug)


def register_hook() -> None:
    if launch_hook not in commands.LAUNCH_HOOKS:
        commands.LAUNCH_HOOKS.append(launch_hook)


def unregister_hook() -> None:
    if launch_hook in commands.LAUNCH_HOOKS:
        commands.LAUNCH_HOOKS.remove(launch_hook)


async def loop(
    interval_s: int = 30,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Plan, launch, reap — forever, one fresh session per iteration."""
    while True:
        try:
            with db.SessionLocal() as session:
                for bot, slot in plan(session):
                    launch(session, bot, slot=slot)
                reap(session)
        except asyncio.CancelledError:
            raise
        except Exception:  # the loop outlives any single failure
            log.exception("supervisor iteration failed")
        await sleep(interval_s)
