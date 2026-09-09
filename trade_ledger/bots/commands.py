"""Command queue: issuing and acknowledging `BotCommand` rows.

`issue` is the one place that knows the guards — a resume with no reason, an
unconfirmed flat, or a second pending command of the same kind are all
rejected here rather than in every caller (the UI route, Telegram, the
monitor). `ack` is the flag semantics the push protocol applies when a bot
confirms a command; `api/bot_push.py` calls it too, so there is one
implementation of "what an acked command does to the bot".
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import BotHost, CommandKind, EventKind
from ..models import Bot, BotCommand, BotEvent

# The local supervisor registers its launch function here. `issue`
# calls every hook when a `run_now` lands on a local bot, so a queued
# command launches immediately instead of waiting for the next schedule
# tick. ponytail: a plain list rather than a pub/sub bus — one hook today.
LAUNCH_HOOKS: list[Callable[[Bot], None]] = []

# Which bot flag an acknowledged command flips. `flat` pauses entries too: a
# bot that just closed everything must not re-enter on the next bar.
ACK_FLAGS = {
    CommandKind.FLAT: ("paused_entries", True),
    CommandKind.PAUSE: ("paused_entries", True),
    CommandKind.RESUME: ("paused_entries", False),
    CommandKind.DRY_RUN_ON: ("dry_run", True),
    CommandKind.DRY_RUN_OFF: ("dry_run", False),
}


class CommandError(Exception):
    """A guard failed. `status_code` is the HTTP status the router maps it to."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC)


def _event(
    session: Session, bot: Bot, kind: EventKind, message: str = "", payload: dict | None = None
) -> None:
    session.add(
        BotEvent(
            bot_id=bot.id,
            ts=_now(),
            kind=kind,
            message=message,
            payload_json=json.dumps(payload or {}),
        )
    )


def issue(
    session: Session,
    bot: Bot,
    kind: CommandKind,
    reason: str | None = None,
    issued_by: str = "ui",
    confirm: str | None = None,
) -> BotCommand:
    """Queue a command for the bot to pick up at its next `GET config`.

    Raises `CommandError` (422) when `resume` has no reason or `flat` is not
    confirmed with the bot's own slug, and (409) when a command of the same
    kind is already pending — the operator acts on the one already queued
    rather than piling up duplicates.
    """
    if kind == CommandKind.RESUME and not reason:
        raise CommandError(422, "resume requires a reason")
    if kind == CommandKind.FLAT and confirm != bot.slug:
        raise CommandError(422, "flat requires confirm to equal the bot's slug")

    pending = session.execute(
        select(BotCommand.id).where(
            BotCommand.bot_id == bot.id,
            BotCommand.kind == kind,
            BotCommand.acked_ts.is_(None),
        )
    ).scalar_one_or_none()
    if pending is not None:
        raise CommandError(409, f"a {kind} command is already pending")

    command = BotCommand(bot_id=bot.id, kind=kind, reason=reason, issued_by=issued_by)
    session.add(command)
    session.commit()
    session.refresh(command)

    if kind == CommandKind.RUN_NOW and bot.host == BotHost.LOCAL:
        for hook in LAUNCH_HOOKS:
            hook(bot)

    return command


def ack(session: Session, command: BotCommand, result: str, detail: str | None = None) -> None:
    """The bot confirms it carried a command out. Only a result of `ok`
    flips a flag — a failed pause must stay pending in the operator's view.
    """
    bot = session.get(Bot, command.bot_id)
    command.acked_ts = _now()
    command.result = result
    command.result_detail = detail
    if result == "ok" and command.kind in ACK_FLAGS:
        field, value = ACK_FLAGS[command.kind]
        setattr(bot, field, value)
    _event(
        session,
        bot,
        EventKind.COMMAND,
        message=f"{command.kind} acked: {result}",
        payload={"command_id": command.id, "kind": command.kind, "result": result},
    )
    session.commit()
