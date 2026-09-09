"""The app's own opinion of every bot, refreshed on a timer.

`health` is the read: pure, no writes, safe to call from a GET. `evaluate`
is `health` plus its consequences — alerts, system commands and the cached
`bot.status`. The loop calls `evaluate_all` every 60 s so a bot that goes
silent is noticed even though a silent bot pushes nothing to notice.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import db
from ..enums import AlertSeverity, BotStatus, CommandKind, EventKind
from ..models import Account, Bot, BotCommand, BotEvent, BotRun, BotState
from . import commands, kill_rules, status
from .alerts import COOLDOWN, raise_alert
from .capital import stage_capital_eur
from .schedule import deadline, next_run

log = logging.getLogger(__name__)


def _run_summary(run: BotRun | None) -> dict | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "started": run.started,
        "finished": run.finished,
        "status": run.status,
        "error": run.error,
    }


def _state_summary(state: BotState | None) -> dict | None:
    if state is None:
        return None
    positions = kill_rules.positions(state)
    return {
        "ts": state.ts,
        "equity_eur": state.equity_eur,
        "peak_equity_eur": state.peak_equity_eur,
        "drawdown_pct": kill_rules.drawdown_pct(state),
        "reconciliation": state.reconciliation,
        "reconciliation_detail": state.reconciliation_detail,
        "positions": positions,
        "positions_count": len(positions),
        "positions_without_stop": sum(1 for p in positions if p.get("stop_present") is False),
        "config_version": state.config_version,
    }


def get_state(session: Session, bot: Bot) -> BotState | None:
    return session.execute(select(BotState).where(BotState.bot_id == bot.id)).scalar_one_or_none()


def get_last_run(session: Session, bot: Bot) -> BotRun | None:
    if bot.last_run_id is None:
        return None
    run = session.get(BotRun, bot.last_run_id)
    return run if run is not None and run.bot_id == bot.id else None


def health(session: Session, bot: Bot, now: datetime | None = None) -> dict:
    """Everything `GET /health` answers. Reads only — no alert, no command."""
    now = now or datetime.now(UTC)
    state = get_state(session, bot)
    last_run = get_last_run(session, bot)
    account = session.get(Account, bot.account_id)
    capital = stage_capital_eur(session, bot, account.mode)
    rules = kill_rules.evaluate_all(
        bot, state, status.bot_trades(session, bot), now, capital, last_run=last_run
    )
    return {
        "status": status.derive_status(bot, state, last_run, now),
        "kill_rules": rules,
        "last_heartbeat": bot.last_heartbeat,
        "next_run": next_run(bot.schedule_every_s, bot.schedule_at, now),
        "deadline": deadline(bot),
        "last_run": _run_summary(last_run),
        "state": _state_summary(state),
        "stage_capital_eur": capital,
    }


def _queue(session: Session, bot: Bot, kind: CommandKind, reason: str, now: datetime) -> None:
    """Queue a system command, unless one just like it is recent enough.

    The cooldown is the alert cooldown, and for the same reason: a bot that
    acknowledged a `pause` as *failed* leaves nothing pending, so `issue`'s
    own duplicate guard would let the monitor re-queue it every minute.
    Six hours of quiet matches what the operator sees in the alert list.
    """
    recent = session.execute(
        select(BotCommand.id).where(
            BotCommand.bot_id == bot.id,
            BotCommand.kind == kind,
            BotCommand.issued_ts >= now - COOLDOWN,
        )
    ).first()
    if recent is not None:
        return
    try:
        # `confirm` is an operator guard against a fat-fingered emergency
        # flat; the monitor is the system, and it already knows the slug.
        command = commands.issue(
            session, bot, kind, reason=reason, issued_by="system", confirm=bot.slug
        )
    except commands.CommandError as exc:
        if exc.status_code != 409:  # not "already pending" — a real guard failed
            raise
        return
    # `issue` stamps its own wall clock; the monitor's `now` is the time this
    # evaluation is about, and the cooldown above measures against it.
    command.issued_ts = now


def evaluate(session: Session, bot: Bot, now: datetime | None = None) -> dict:
    """Health, plus what the app does about it. Commits."""
    now = now or datetime.now(UTC)
    report = health(session, bot, now)
    bot.status = report["status"]

    if not bot.enabled:
        # A bot the operator switched off is not misbehaving, it is off.
        # Alerting on it forever would train them to ignore the alert list.
        session.commit()
        return report

    for rule in report["kill_rules"]:
        if rule["status"] == "ok":
            continue
        severity = (
            AlertSeverity.CRITICAL
            if rule["rule"] in kill_rules.CRITICAL_RULES
            else AlertSeverity.WARNING
        )
        alert = raise_alert(
            session,
            bot,
            severity,
            f"kill_{rule['rule'].lower()}",
            f"{rule['rule']}: {rule['detail']}",
            now=now,
        )
        if alert is None:
            # Deduped: the rule was already firing, so nothing changed and the
            # timeline has the entry already. ponytail: alert creation is the
            # only "is this new?" state we keep — no separate per-rule history.
            continue
        session.add(
            BotEvent(
                bot_id=bot.id,
                ts=now,
                kind=EventKind.KILL_RULE,
                message=f"{rule['rule']} {rule['status']}: {rule['detail']}",
                payload_json=json.dumps(rule),
            )
        )
        if rule["action"] == "pause" and not bot.paused_entries:
            _queue(session, bot, CommandKind.PAUSE, "K1 capital brake", now)
        # A `flat` for a bot nobody can reach would sit unacknowledged and
        # tell the operator nothing; the critical K5 alert is the answer there.
        if rule["action"] == "flat" and report["status"] != BotStatus.STALE:
            _queue(session, bot, CommandKind.FLAT, "K4 naked position", now)

    session.commit()
    return report


def evaluate_all(session: Session, now: datetime | None = None) -> list[dict]:
    """Every bot, one at a time. A bot that blows up does not stop the rest."""
    now = now or datetime.now(UTC)
    reports = []
    for bot in session.execute(select(Bot).order_by(Bot.id)).scalars().all():
        try:
            reports.append(evaluate(session, bot, now))
        except Exception:  # one bad bot must not blind the fleet
            log.exception("monitor failed for bot %s", bot.slug)
            session.rollback()
    return reports


async def loop(
    interval_s: int = 60,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Evaluate the fleet forever, one fresh session per iteration.

    Cancelled by the app lifespan on shutdown; `sleep` is injectable so a
    test can run exactly one iteration.
    """
    while True:
        try:
            with db.SessionLocal() as session:
                evaluate_all(session)
        except asyncio.CancelledError:
            raise
        except Exception:  # the loop outlives any single failure
            log.exception("monitor iteration failed")
        await sleep(interval_s)
