"""Telegram: outbound alerts and the daily summary, inbound commands.

Everything here degrades to a no-op when `TELEGRAM_BOT_TOKEN` or
`TELEGRAM_CHAT_ID` is unset — `send` returns `False`, `poll_once` returns the
offset unchanged. Nothing in this module ever raises out of a network call;
a broken Telegram connection must not take the monitor or the app down with
it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import db
from ..enums import AlertSeverity, CommandKind
from ..models import Alert, Bot
from ..settings import get_settings
from . import commands, monitor

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"

_KIND_BY_WORD = {
    "pause": CommandKind.PAUSE,
    "resume": CommandKind.RESUME,
    "flat": CommandKind.FLAT,
    "runnow": CommandKind.RUN_NOW,
}


def send(text: str, client: httpx.Client | None = None) -> bool:
    """POST `sendMessage`, plain text — nothing here emits markup, and a
    stray `<`/`&` in a bot name or message would otherwise make Telegram
    reject the whole send. `False` when unconfigured or the call fails —
    never raises, so a caller mid-transaction (a notifier, `poll_once`'s
    reply) never has to guard it.

    Never logs the exception object or the request URL: both carry the bot
    token (`https://api.telegram.org/bot<token>/...`). Only the method and,
    once there is a response, its status code.
    """
    settings = get_settings()
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return False
    owns_client = client is None
    client = client or httpx.Client()
    try:
        try:
            response = client.post(
                _API.format(token=settings.TELEGRAM_BOT_TOKEN, method="sendMessage"),
                json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": text},
            )
        except httpx.HTTPError as exc:
            log.warning("telegram sendMessage POST failed: %s", type(exc).__name__)
            return False
        if response.status_code >= 400:
            log.warning("telegram sendMessage failed: status=%s", response.status_code)
            return False
        return True
    finally:
        if owns_client:
            client.close()


def format_alert(alert: Alert, bot: Bot | None) -> str:
    name = bot.name if bot is not None else "system"
    return f"[{str(alert.severity).upper()}] {name}: {alert.message}"


def notify(alert: Alert, bot: Bot | None) -> None:
    """Registered in `alerts.NOTIFIERS` by `main.py` when Telegram is
    configured. Warning and critical only — info alerts are for the UI, not
    a phone buzzing. Marks `alert.sent_telegram` on success; the caller
    (`raise_alert`, inside the same transaction) commits it.
    """
    if alert.severity not in (AlertSeverity.WARNING, AlertSeverity.CRITICAL):
        return
    if send(format_alert(alert, bot)):
        alert.sent_telegram = True


def daily_summary(session: Session) -> str:
    """One line per bot: status, equity, drawdown, open positions, kill
    rules currently not `ok`.
    """
    bots = session.execute(select(Bot).order_by(Bot.id)).scalars().all()
    if not bots:
        return "No bots configured."
    lines = ["Daily summary:"]
    for bot in bots:
        report = monitor.health(session, bot)
        state = report["state"] or {}
        firing = [rule["rule"] for rule in report["kill_rules"] if rule["status"] != "ok"]
        lines.append(
            f"{bot.name} [{report['status']}] "
            f"equity={state.get('equity_eur', '-')} "
            f"dd={state.get('drawdown_pct', '-')}% "
            f"positions={state.get('positions_count', 0)} "
            f"kill={','.join(firing) if firing else 'ok'}"
        )
    return "\n".join(lines)


def parse_command(text: str) -> tuple[str, str | None, str | None] | None:
    """One inbound message -> `(kind, slug, arg)`, or `None` when it is not
    a recognised command.

    `kind` is a `CommandKind` value, except `"status"` (no slug, no arg).
    A malformed command (missing slug, wrong argument count) is `None`
    rather than a partial match, so a garbled command reads as a stray
    message rather than a half-parsed one. `flat` without `CONFIRM` still
    parses — `poll_once` passes it through to `commands.issue`, which is the
    one place that already knows the confirm guard.
    """
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    word, _, rest = text.partition(" ")
    word = word[1:].split("@", 1)[0].lower()  # strip a /cmd@BotName suffix
    rest = rest.strip()

    if word == "status":
        return None if rest else ("status", None, None)

    if word not in _KIND_BY_WORD:
        return None
    kind = _KIND_BY_WORD[word]

    if word in ("pause", "runnow"):
        tokens = rest.split()
        return (kind, tokens[0], None) if len(tokens) == 1 else None

    if word == "resume":
        slug, _, reason = rest.partition(" ")
        reason = reason.strip()
        return (kind, slug, reason) if slug and reason else None

    # flat
    tokens = rest.split()
    if len(tokens) == 1:
        return (kind, tokens[0], None)
    if len(tokens) == 2:
        return (kind, tokens[0], tokens[1])
    return None


def _handle_command(session: Session, client: httpx.Client, text: str) -> None:
    parsed = parse_command(text)
    if parsed is None:
        return
    kind, slug, arg = parsed

    if kind == "status":
        send(daily_summary(session), client=client)
        return

    bot = session.execute(select(Bot).where(Bot.slug == slug)).scalar_one_or_none()
    if bot is None:
        send(f"unknown bot: {slug}", client=client)
        return

    reason = arg if kind == CommandKind.RESUME else None
    confirm = slug if kind == CommandKind.FLAT and arg == "CONFIRM" else None
    try:
        commands.issue(session, bot, kind, reason=reason, issued_by="telegram", confirm=confirm)
        send(f"{kind} queued for {slug}", client=client)
    except commands.CommandError as exc:
        send(exc.detail, client=client)


def poll_once(session: Session, client: httpx.Client, offset: int) -> int:
    """One `getUpdates` round. Returns the offset for the next call — the
    highest `update_id` seen plus one, so a delivered update is never
    replayed even when it was ignored (a foreign chat, a malformed command,
    or one that blew up while being handled).

    Never logs the exception object or the request URL — see `send`.
    """
    settings = get_settings()
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return offset

    try:
        response = client.get(
            _API.format(token=settings.TELEGRAM_BOT_TOKEN, method="getUpdates"),
            params={"offset": offset, "timeout": 0},
        )
    except httpx.HTTPError as exc:
        log.warning("telegram getUpdates GET failed: %s", type(exc).__name__)
        return offset
    if response.status_code >= 400:
        log.warning("telegram getUpdates failed: status=%s", response.status_code)
        return offset
    updates = response.json().get("result", [])

    for update in updates:
        offset = update["update_id"] + 1
        message = update.get("message") or {}
        text = message.get("text")
        if not text:
            continue
        if str(message.get("chat", {}).get("id")) != str(settings.TELEGRAM_CHAT_ID):
            continue
        try:
            _handle_command(session, client, text)
        except Exception:
            # A bad update (a DB hiccup, an unexpected shape) must not stall
            # the offset — that would replay the same update forever.
            log.exception("telegram command handling failed")

    return offset


async def loop(
    interval_s: int = 5,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Poll forever: inbound commands, plus the daily summary once per local
    day at 07:00. `last_summary_date` is in-memory only — a restart right at
    07:00 can send the summary twice. # ponytail: good enough for one
    operator's phone; a persisted "last sent" row is the fix if that ever
    actually bites.
    """
    offset = 0
    last_summary_date = None
    with httpx.Client() as client:
        while True:
            try:
                with db.SessionLocal() as session:
                    offset = poll_once(session, client, offset)
                    now = datetime.now().astimezone()
                    if now.hour == 7 and last_summary_date != now.date():
                        send(daily_summary(session), client=client)
                        last_summary_date = now.date()
            except asyncio.CancelledError:
                raise
            except Exception:  # the loop outlives any single failure
                log.exception("telegram loop iteration failed")
            await sleep(interval_s)
