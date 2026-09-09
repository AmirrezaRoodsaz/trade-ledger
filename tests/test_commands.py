from __future__ import annotations

import pytest

from trade_ledger.bots import commands
from trade_ledger.bots.commands import CommandError
from trade_ledger.enums import CommandKind
from trade_ledger.models import BotCommand

# --- issue: guards ----------------------------------------------------------


def test_resume_without_a_reason_is_rejected(session, bot_factory):
    bot, _ = bot_factory()
    with pytest.raises(CommandError) as exc_info:
        commands.issue(session, bot, CommandKind.RESUME, reason=None, issued_by="ui")
    assert exc_info.value.status_code == 422


def test_resume_with_a_reason_is_queued(session, bot_factory):
    bot, _ = bot_factory()
    command = commands.issue(
        session, bot, CommandKind.RESUME, reason="drawdown cleared", issued_by="ui"
    )
    assert command.reason == "drawdown cleared"
    assert command.acked_ts is None


def test_flat_without_confirm_is_rejected(session, bot_factory):
    bot, _ = bot_factory()
    with pytest.raises(CommandError) as exc_info:
        commands.issue(session, bot, CommandKind.FLAT, issued_by="ui", confirm=None)
    assert exc_info.value.status_code == 422


def test_flat_with_the_wrong_slug_is_rejected(session, bot_factory):
    bot, _ = bot_factory()
    with pytest.raises(CommandError) as exc_info:
        commands.issue(session, bot, CommandKind.FLAT, issued_by="ui", confirm="not-the-slug")
    assert exc_info.value.status_code == 422


def test_flat_with_the_matching_slug_is_queued(session, bot_factory):
    bot, _ = bot_factory()
    command = commands.issue(session, bot, CommandKind.FLAT, issued_by="ui", confirm=bot.slug)
    assert command.kind == CommandKind.FLAT


def test_duplicate_pending_command_is_rejected(session, bot_factory):
    bot, _ = bot_factory()
    commands.issue(session, bot, CommandKind.PAUSE, issued_by="ui")
    with pytest.raises(CommandError) as exc_info:
        commands.issue(session, bot, CommandKind.PAUSE, issued_by="ui")
    assert exc_info.value.status_code == 409


def test_a_new_pause_is_allowed_once_the_old_one_is_acked(session, bot_factory):
    bot, _ = bot_factory()
    first = commands.issue(session, bot, CommandKind.PAUSE, issued_by="ui")
    commands.ack(session, first, "ok")
    second = commands.issue(session, bot, CommandKind.PAUSE, issued_by="ui")
    assert second.id != first.id


# --- ack: flag semantics -----------------------------------------------------


def test_resume_clears_paused_entries_only_on_ack(session, bot_factory):
    bot, _ = bot_factory(paused_entries=True)
    command = commands.issue(session, bot, CommandKind.RESUME, reason="all clear", issued_by="ui")
    assert bot.paused_entries is True  # issuing alone does not flip the flag

    commands.ack(session, command, "ok")
    assert bot.paused_entries is False


def test_a_failed_ack_does_not_flip_the_flag(session, bot_factory):
    bot, _ = bot_factory()
    command = commands.issue(session, bot, CommandKind.PAUSE, issued_by="ui")
    commands.ack(session, command, "error", "could not reach exchange")
    assert bot.paused_entries is False
    assert command.result == "error"


# --- run_now launch hook ------------------------------------------------------


def test_run_now_on_a_local_bot_calls_a_registered_hook(session, bot_factory):
    from trade_ledger.enums import BotHost

    bot, _ = bot_factory(host=BotHost.LOCAL)
    called = []
    hook = called.append
    commands.LAUNCH_HOOKS.append(hook)
    try:
        commands.issue(session, bot, CommandKind.RUN_NOW, issued_by="ui")
    finally:
        commands.LAUNCH_HOOKS.remove(hook)
    assert called == [bot]


def test_run_now_on_a_remote_bot_does_not_call_the_hook(session, bot_factory):
    from trade_ledger.enums import BotHost

    bot, _ = bot_factory(host=BotHost.REMOTE)
    called = []
    hook = called.append
    commands.LAUNCH_HOOKS.append(hook)
    try:
        commands.issue(session, bot, CommandKind.RUN_NOW, issued_by="ui")
    finally:
        commands.LAUNCH_HOOKS.remove(hook)
    assert called == []


# --- API routes ---------------------------------------------------------------


def test_create_command_route(client, bot_factory):
    bot, _ = bot_factory()
    resp = client.post(f"/api/bots/{bot.slug}/commands", json={"kind": "pause"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "pause"
    assert body["issued_by"] == "ui"


def test_create_command_route_resume_without_reason_is_422(client, bot_factory):
    bot, _ = bot_factory()
    resp = client.post(f"/api/bots/{bot.slug}/commands", json={"kind": "resume"})
    assert resp.status_code == 422


def test_create_command_route_flat_without_confirm_is_422(client, bot_factory):
    bot, _ = bot_factory()
    resp = client.post(f"/api/bots/{bot.slug}/commands", json={"kind": "flat"})
    assert resp.status_code == 422


def test_create_command_route_duplicate_pending_is_409(client, bot_factory):
    bot, _ = bot_factory()
    first = client.post(f"/api/bots/{bot.slug}/commands", json={"kind": "pause"})
    assert first.status_code == 201
    second = client.post(f"/api/bots/{bot.slug}/commands", json={"kind": "pause"})
    assert second.status_code == 409


def test_list_commands_route(client, session, bot_factory):
    bot, _ = bot_factory()
    for _ in range(3):
        cmd = BotCommand(bot_id=bot.id, kind=CommandKind.PAUSE, acked_ts=None)
        session.add(cmd)
        session.flush()
        commands.ack(session, cmd, "ok")

    resp = client.get(f"/api/bots/{bot.slug}/commands?limit=2")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    # newest first
    assert body[0]["id"] > body[1]["id"]


def test_commands_route_unknown_bot_is_404(client):
    resp = client.post("/api/bots/ghost/commands", json={"kind": "pause"})
    assert resp.status_code == 404
