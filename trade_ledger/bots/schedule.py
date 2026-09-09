"""Schedule arithmetic. All times are UTC.

A bot fires at `schedule_at` (an "HH:MM" anchor) and every
`schedule_every_s` seconds from there, so a 4-hour bot anchored at 00:05
runs at 00:05, 04:05, 08:05 and 12:05.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from ..models import Bot


def next_run(every_s: int, at: str, now: datetime) -> datetime:
    """The first schedule slot strictly after `now`.

    Strictly: called exactly on a slot, the answer is the following one, so a
    bot that just ran is never told to run again immediately.
    """
    if every_s <= 0:
        raise ValueError("schedule_every_s must be positive")
    hour, _, minute = at.partition(":")
    now = now.astimezone(UTC)
    anchor = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    steps = math.floor((now - anchor).total_seconds() / every_s) + 1
    return anchor + timedelta(seconds=steps * every_s)


def deadline(bot: Bot, now: datetime | None = None) -> datetime:
    """When the bot's next heartbeat becomes overdue (K5).

    Counted from the last heartbeat, or from creation for a bot that has
    never reported. `now` is accepted for call-site symmetry with `next_run`
    and deliberately unused — the deadline does not depend on it.
    """
    last = bot.last_heartbeat or bot.created
    return last.astimezone(UTC) + timedelta(seconds=bot.schedule_every_s + bot.grace_s)
