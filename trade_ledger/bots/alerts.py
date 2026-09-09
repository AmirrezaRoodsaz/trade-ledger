"""Alerts, with the cooldown that keeps a stuck bot from becoming spam.

One alert per (bot, kind) per 6 hours. Acknowledging one does not reopen the
window: a rule that is still firing an hour later is the same news, and the
operator already saw it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import AlertSeverity
from ..models import Alert, Bot

log = logging.getLogger(__name__)

COOLDOWN = timedelta(hours=6)

# Task 5 appends the Telegram sender here. Called with the freshly created
# alert and its bot; a notifier that raises is logged, never propagated —
# the alert row already exists and losing it to a network error would be
# worse than a missed message.
NOTIFIERS: list[Callable[[Alert, Bot | None], None]] = []


def raise_alert(
    session: Session,
    bot: Bot | None,
    severity: AlertSeverity | str,
    kind: str,
    message: str,
    now: datetime | None = None,
) -> Alert | None:
    """A new alert, or `None` when an identical one is still in cooldown.

    Flushes but does not commit — the caller decides the transaction.
    """
    now = now or datetime.now(UTC)
    bot_id = bot.id if bot is not None else None
    stmt = select(Alert.id).where(Alert.kind == kind, Alert.ts >= now - COOLDOWN)
    stmt = stmt.where(Alert.bot_id == bot_id if bot_id is not None else Alert.bot_id.is_(None))
    if session.execute(stmt).first() is not None:
        return None

    alert = Alert(bot_id=bot_id, ts=now, severity=severity, kind=kind, message=message)
    session.add(alert)
    session.flush()
    for notify in NOTIFIERS:
        try:
            notify(alert, bot)
        except Exception:  # a notifier must not lose the alert
            log.exception("alert notifier failed for %s", kind)
    return alert
