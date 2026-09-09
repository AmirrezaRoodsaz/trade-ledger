from __future__ import annotations

import json
import logging

import httpx
import pytest
from sqlalchemy import select

from trade_ledger.bots import telegram
from trade_ledger.enums import AlertSeverity, CommandKind
from trade_ledger.models import Alert, BotCommand

TOKEN = "123:ABC"
CHAT_ID = "999"


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT_ID)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- send --------------------------------------------------------------------


def test_send_posts_to_the_right_url_with_the_right_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    ok = telegram.send("hello", client=_client(handler))

    assert ok is True
    assert seen["url"] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert seen["body"] == {"chat_id": CHAT_ID, "text": "hello"}


def test_send_returns_false_when_unconfigured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert telegram.send("hello") is False


def test_send_returns_false_on_a_failed_request():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    assert telegram.send("hello", client=_client(handler)) is False


def test_send_never_logs_the_token_on_a_failed_request(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with caplog.at_level(logging.WARNING):
        assert telegram.send("hello", client=_client(handler)) is False

    assert TOKEN not in caplog.text


def test_send_sends_plain_text_no_parse_mode_even_with_markup_looking_text():
    """A `<b>` in a bot name or message must reach Telegram as literal text,
    not be interpreted as HTML — Telegram rejects unbalanced/unescaped
    markup outright, so `parse_mode` must not be set at all.
    """
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    ok = telegram.send("<b>bold</b> & stuff", client=_client(handler))

    assert ok is True
    assert seen["body"] == {"chat_id": CHAT_ID, "text": "<b>bold</b> & stuff"}
    assert "parse_mode" not in seen["body"]


# --- format_alert --------------------------------------------------------------


def test_format_alert_contains_bot_name_severity_and_message(bot_factory):
    bot, _ = bot_factory(name="Donchian Bot")
    alert = Alert(bot_id=bot.id, severity=AlertSeverity.CRITICAL, kind="kill_k1", message="down 20%")

    text = telegram.format_alert(alert, bot)

    assert "Donchian Bot" in text
    assert "CRITICAL" in text
    assert "down 20%" in text


# --- parse_command -------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/status", ("status", None, None)),
        ("/status extra", None),
        ("/pause okx-donchian", (CommandKind.PAUSE, "okx-donchian", None)),
        ("/pause", None),
        ("/pause a b", None),
        ("/resume okx-donchian drawdown cleared", (CommandKind.RESUME, "okx-donchian", "drawdown cleared")),
        ("/resume okx-donchian", None),
        ("/resume", None),
        ("/flat okx-donchian CONFIRM", (CommandKind.FLAT, "okx-donchian", "CONFIRM")),
        ("/flat okx-donchian", (CommandKind.FLAT, "okx-donchian", None)),
        ("/flat", None),
        ("/flat a b c", None),
        ("/runnow okx-donchian", (CommandKind.RUN_NOW, "okx-donchian", None)),
        ("/runnow", None),
        ("not a command", None),
        ("", None),
        ("/bogus okx-donchian", None),
    ],
)
def test_parse_command_table(text, expected):
    assert telegram.parse_command(text) == expected


# --- poll_once -----------------------------------------------------------------


def _updates_response(updates: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": updates})


def _message(update_id: int, chat_id, text: str) -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


def test_poll_once_ignores_a_foreign_chat_issues_a_pause_and_replies(session, bot_factory):
    bot, _ = bot_factory()
    replies = []
    updates = [
        _message(1, "666", "/pause " + bot.slug),  # foreign chat
        _message(2, CHAT_ID, "/pause " + bot.slug),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return _updates_response(updates)
        replies.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})

    next_offset = telegram.poll_once(session, _client(handler), 0)

    assert next_offset == 3
    pauses = session.execute(
        select(BotCommand).where(BotCommand.bot_id == bot.id, BotCommand.kind == CommandKind.PAUSE)
    ).scalars().all()
    assert len(pauses) == 1
    assert pauses[0].issued_by == "telegram"
    assert len(replies) == 1
    assert bot.slug in replies[0] or "pause" in replies[0]


def test_poll_once_flat_without_confirm_replies_with_error_and_issues_nothing(session, bot_factory):
    bot, _ = bot_factory()
    replies = []
    updates = [_message(1, CHAT_ID, "/flat " + bot.slug)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return _updates_response(updates)
        replies.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})

    telegram.poll_once(session, _client(handler), 0)

    flats = session.execute(
        select(BotCommand).where(BotCommand.bot_id == bot.id, BotCommand.kind == CommandKind.FLAT)
    ).scalars().all()
    assert flats == []
    assert len(replies) == 1
    assert "confirm" in replies[0].lower()


def test_poll_once_flat_with_confirm_queues_the_command_and_replies(session, bot_factory):
    bot, _ = bot_factory()
    replies = []
    updates = [_message(1, CHAT_ID, f"/flat {bot.slug} CONFIRM")]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return _updates_response(updates)
        replies.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})

    telegram.poll_once(session, _client(handler), 0)

    flats = (
        session.execute(
            select(BotCommand).where(BotCommand.bot_id == bot.id, BotCommand.kind == CommandKind.FLAT)
        )
        .scalars()
        .all()
    )
    assert len(flats) == 1
    assert flats[0].issued_by == "telegram"
    assert len(replies) == 1
    assert "confirm" not in replies[0].lower()


def test_poll_once_status_replies_with_the_daily_summary(session, bot_factory):
    bot, _ = bot_factory()
    replies = []
    updates = [_message(1, CHAT_ID, "/status")]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return _updates_response(updates)
        replies.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})

    telegram.poll_once(session, _client(handler), 0)

    assert len(replies) == 1
    assert bot.name in replies[0]


def test_poll_once_returns_the_offset_unchanged_when_unconfigured(session, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call Telegram when unconfigured")

    assert telegram.poll_once(session, _client(handler), 5) == 5


# --- daily_summary ---------------------------------------------------------------


def test_daily_summary_lists_every_bot(session, bot_factory):
    bot_factory(name="Bot One")
    bot_factory(name="Bot Two")

    text = telegram.daily_summary(session)

    assert "Bot One" in text
    assert "Bot Two" in text


# --- notify --------------------------------------------------------------------


def test_notify_sends_warning_and_critical_and_marks_sent(bot_factory, monkeypatch):
    bot, _ = bot_factory()
    sent = []

    def fake_send(text, client=None):
        sent.append(text)
        return True

    monkeypatch.setattr(telegram, "send", fake_send)

    alert = Alert(bot_id=bot.id, severity=AlertSeverity.WARNING, kind="kill_k2", message="pf low")
    telegram.notify(alert, bot)
    assert alert.sent_telegram is True
    assert sent == [telegram.format_alert(alert, bot)]

    sent.clear()
    info_alert = Alert(bot_id=bot.id, severity=AlertSeverity.INFO, kind="info", message="fyi")
    telegram.notify(info_alert, bot)
    assert sent == []
    assert not info_alert.sent_telegram


# --- settings endpoint -----------------------------------------------------------


def test_telegram_test_endpoint_reports_not_configured_without_keys(client, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    response = client.post("/api/settings/telegram/test")

    assert response.status_code == 200
    assert response.json() == {"ok": False, "detail": "not configured"}


def _patch_client_transport(monkeypatch, handler) -> None:
    """`send()` builds its own `httpx.Client()` when called with no client —
    the route calls it that way, so the test patches the class itself to
    hand back one wired to a `MockTransport`.
    """
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: real_client(transport=httpx.MockTransport(handler)))


def test_telegram_test_endpoint_success(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    _patch_client_transport(monkeypatch, handler)

    response = client.post("/api/settings/telegram/test")

    assert response.json() == {"ok": True, "detail": "sent"}


def test_telegram_test_endpoint_send_failure(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    _patch_client_transport(monkeypatch, handler)

    response = client.post("/api/settings/telegram/test")

    assert response.json() == {"ok": False, "detail": "send failed"}
