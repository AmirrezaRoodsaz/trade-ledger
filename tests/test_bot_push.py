from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_ledger.enums import CommandKind, EventKind, Mode, RunStatus, TradeStatus
from trade_ledger.models import (
    BotCommand,
    BotEvent,
    BotRun,
    BotState,
    Preset,
    PresetVersion,
    Setting,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _events(session, bot, kind: EventKind) -> list[BotEvent]:
    return [e for e in session.query(BotEvent).all() if e.bot_id == bot.id and e.kind == kind]


# --- authentication ---------------------------------------------------------


def test_push_without_a_token_is_401(client, bot_factory):
    bot, _ = bot_factory()
    resp = client.post(f"/api/bots/{bot.slug}/heartbeat", json={})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "bot token required"


def test_push_with_a_bad_token_is_401(client, bot_factory):
    bot, _ = bot_factory()
    resp = client.post(f"/api/bots/{bot.slug}/heartbeat", json={}, headers=_auth("nope"))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid bot token"


def test_a_token_cannot_push_to_another_bot(client, bot_factory):
    _, token = bot_factory(name="one")
    other, _ = bot_factory(name="two")
    resp = client.post(f"/api/bots/{other.slug}/heartbeat", json={}, headers=_auth(token))
    assert resp.status_code == 403


def test_config_of_an_unknown_slug_is_403_not_a_leak(client, bot_factory):
    _, token = bot_factory()
    assert client.get("/api/bots/ghost/config", headers=_auth(token)).status_code == 403


# --- heartbeat, runs, state, events -----------------------------------------


def test_round_trip_heartbeat_run_state_events(client, session, bot_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    bot, token = bot_factory()
    headers = _auth(token)

    beat = client.post(
        f"/api/bots/{bot.slug}/heartbeat",
        json={
            "ts": "2026-09-09T10:00:00+00:00",
            "host": "local",
            "code_version": "abc1234",
            "next_run": "2026-09-09T12:05:00+00:00",
        },
        headers=headers,
    )
    assert beat.status_code == 200, beat.text
    assert bot.last_heartbeat == datetime(2026, 9, 9, 10, 0, tzinfo=UTC)
    assert bot.code_version == "abc1234"
    heartbeats = _events(session, bot, EventKind.HEARTBEAT)
    assert len(heartbeats) == 1
    assert json.loads(heartbeats[0].payload_json) == {"next_run": "2026-09-09T12:05:00+00:00"}

    started = client.post(
        f"/api/bots/{bot.slug}/runs", json={"started": "2026-09-09T12:05:00+00:00"}, headers=headers
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]
    assert bot.last_run_id == run_id
    assert session.get(BotRun, run_id).status == RunStatus.RUNNING

    finished = client.post(
        f"/api/bots/{bot.slug}/state",
        json={
            "equity_eur": "2500",
            "positions": [
                {
                    "symbol": "BTC/USDT:USDT",
                    "qty": "0.01",
                    "avg_entry": "60000",
                    "stop_present": True,
                    "stop_price": "57000",
                    "unrealised_eur": "12.5",
                }
            ],
            "open_orders": [],
            "reconciliation": "ok",
            "config_version": 2,
            "extra": {"bars": 120},
        },
        headers=headers,
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["peak_equity_eur"] == "2500"
    state = session.query(BotState).filter_by(bot_id=bot.id).one()
    assert json.loads(state.positions_json)[0]["stop_present"] is True
    assert state.config_version == 2
    assert json.loads(state.extra_json) == {"bars": 120}

    pushed = client.post(
        f"/api/bots/{bot.slug}/events",
        json=[
            {"kind": "info", "message": "no signal", "payload": {"symbol": "BTC/USDT:USDT"}},
            {"ts": "2026-09-09T12:06:00+00:00", "kind": "order", "message": "buy 0.01"},
        ],
        headers=headers,
    )
    assert pushed.status_code == 201, pushed.text
    assert pushed.json() == {"inserted": 2}
    assert len(_events(session, bot, EventKind.ORDER)) == 1

    done = client.patch(
        f"/api/bots/{bot.slug}/runs/{run_id}",
        json={"status": "ok", "summary": {"signals": 1}, "log": "line one\nline two\n"},
        headers=headers,
    )
    assert done.status_code == 200, done.text
    run = session.get(BotRun, run_id)
    assert run.status == RunStatus.OK
    assert json.loads(run.summary_json) == {"signals": 1}
    assert run.finished is not None
    log_file = tmp_path / "bots" / bot.slug / "runs" / f"{run_id}.log"
    assert run.log_path == str(log_file)
    assert log_file.read_text() == "line one\nline two\n"


def test_finishing_a_run_with_an_error_emits_an_error_event(client, session, bot_factory):
    bot, token = bot_factory()
    run_id = client.post(f"/api/bots/{bot.slug}/runs", json={}, headers=_auth(token)).json()["id"]
    resp = client.patch(
        f"/api/bots/{bot.slug}/runs/{run_id}",
        json={"status": "error", "error": "exchange timeout"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert session.get(BotRun, run_id).error == "exchange timeout"
    errors = _events(session, bot, EventKind.ERROR)
    assert [e.message for e in errors] == ["exchange timeout"]


def test_a_run_of_another_bot_is_404(client, bot_factory):
    one, one_token = bot_factory(name="one")
    _, two_token = bot_factory(name="two")
    run_id = client.post(f"/api/bots/{one.slug}/runs", json={}, headers=_auth(one_token)).json()[
        "id"
    ]
    resp = client.patch(
        f"/api/bots/two/runs/{run_id}", json={"status": "ok"}, headers=_auth(two_token)
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    ("equities", "expected_peak"),
    [(["1000", "1200", "900"], "1200"), (["900", "800"], "900")],
)
def test_peak_equity_only_grows(client, session, bot_factory, equities, expected_peak):
    bot, token = bot_factory()
    for equity in equities:
        resp = client.post(
            f"/api/bots/{bot.slug}/state", json={"equity_eur": equity}, headers=_auth(token)
        )
        assert resp.status_code == 200, resp.text
    state = session.query(BotState).filter_by(bot_id=bot.id).one()
    assert state.equity_eur == Decimal(equities[-1])
    assert state.peak_equity_eur == Decimal(expected_peak)


def test_a_reconciliation_mismatch_emits_an_event(client, session, bot_factory):
    bot, token = bot_factory()
    resp = client.post(
        f"/api/bots/{bot.slug}/state",
        json={"reconciliation": "mismatch", "reconciliation_detail": "BTC 0.01 vs 0.02"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    events = _events(session, bot, EventKind.RECONCILE)
    assert [e.message for e in events] == ["BTC 0.01 vs 0.02"]
    assert json.loads(events[0].payload_json) == {"reconciliation": "mismatch"}


def test_more_than_500_events_in_one_call_is_413(client, session, bot_factory):
    bot, token = bot_factory()
    batch = [{"kind": "info", "message": f"#{i}"} for i in range(501)]
    resp = client.post(f"/api/bots/{bot.slug}/events", json=batch, headers=_auth(token))
    assert resp.status_code == 413
    assert session.query(BotEvent).count() == 0

    ok = client.post(f"/api/bots/{bot.slug}/events", json=batch[:500], headers=_auth(token))
    assert ok.status_code == 201
    assert ok.json() == {"inserted": 500}


# --- config -----------------------------------------------------------------


def _preset_version(session, **overrides) -> PresetVersion:
    preset = Preset(name="donchian-4h", strategy="donchian")
    session.add(preset)
    session.flush()
    defaults = {
        "preset_id": preset.id,
        "version": 2,
        "params_json": json.dumps({"entry": 55, "exit": 20}),
        "timeframe": "4h",
        "pairs_json": json.dumps(["BTC/USDT:USDT"]),
        "risk_pct": Decimal(3),
        "max_position_pct": Decimal(33),
        "leverage_cap": Decimal(3),
    }
    defaults.update(overrides)
    version = PresetVersion(**defaults)
    session.add(version)
    session.flush()
    return version


def test_config_shape_with_preset_and_pending_commands(
    client, session, bot_factory, account_factory
):
    account = account_factory(mode=Mode.DEMO)
    version = _preset_version(session)
    bot, token = bot_factory(account_id=account.id, preset_version_id=version.id)
    session.add_all(
        [
            BotCommand(
                bot_id=bot.id,
                kind=CommandKind.PAUSE,
                reason="drawdown",
                issued_ts=datetime(2026, 9, 9, 8, 0, tzinfo=UTC),
            ),
            BotCommand(
                bot_id=bot.id,
                kind=CommandKind.RUN_NOW,
                issued_ts=datetime(2026, 9, 9, 7, 0, tzinfo=UTC),
            ),
            BotCommand(
                bot_id=bot.id,
                kind=CommandKind.RESUME,
                issued_ts=datetime(2026, 9, 9, 6, 0, tzinfo=UTC),
                acked_ts=datetime(2026, 9, 9, 6, 1, tzinfo=UTC),
                result="ok",
            ),
        ]
    )
    session.flush()
    # Reload everything as the DB holds it: plain strings, not the enum
    # members this test constructed them from.
    session.expire_all()

    body = client.get(f"/api/bots/{bot.slug}/config", headers=_auth(token)).json()

    assert set(body) == {"bot", "preset", "commands"}
    assert body["bot"] == {
        "slug": bot.slug,
        "account_id": account.id,
        "dry_run": False,
        "paused_entries": False,
        "enabled": True,
        "mode": "demo",
        "venue": "okx",
        # No bot override and no Setting: the demo rung of the ladder.
        "stage_capital_eur": "2500",
    }
    assert body["preset"] == {
        "version_id": version.id,
        "version": 2,
        "strategy": "donchian",
        "params": {"entry": 55, "exit": 20},
        "timeframe": "4h",
        "pairs": ["BTC/USDT:USDT"],
        "risk_pct": "3",
        "max_position_pct": "33",
        "leverage_cap": "3",
    }
    # Pending only, oldest first — the acked resume is gone.
    assert [one["kind"] for one in body["commands"]] == ["run_now", "pause"]
    assert body["commands"][1]["reason"] == "drawdown"


def test_config_without_a_preset_is_null(client, bot_factory):
    bot, token = bot_factory()
    body = client.get(f"/api/bots/{bot.slug}/config", headers=_auth(token)).json()
    assert body["preset"] is None
    assert body["commands"] == []


def test_stage_capital_prefers_the_bot_then_the_setting(
    client, session, bot_factory, account_factory
):
    account = account_factory(mode=Mode.LIVE)
    session.add(Setting(key="stage_capital_live", value="400"))
    bot, token = bot_factory(account_id=account.id)
    session.flush()

    body = client.get(f"/api/bots/{bot.slug}/config", headers=_auth(token)).json()
    assert body["bot"]["stage_capital_eur"] == "400"

    bot.stage_capital_eur = Decimal(150)
    session.flush()
    body = client.get(f"/api/bots/{bot.slug}/config", headers=_auth(token)).json()
    assert body["bot"]["stage_capital_eur"] == "150"


# --- command acknowledgement ------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "field", "before", "after"),
    [
        (CommandKind.FLAT, "paused_entries", False, True),
        (CommandKind.PAUSE, "paused_entries", False, True),
        (CommandKind.RESUME, "paused_entries", True, False),
        (CommandKind.DRY_RUN_ON, "dry_run", False, True),
        (CommandKind.DRY_RUN_OFF, "dry_run", True, False),
    ],
)
def test_ack_flips_the_flag_and_emits_an_event(
    client, session, bot_factory, kind, field, before, after
):
    bot, token = bot_factory(**{field: before})
    command = BotCommand(bot_id=bot.id, kind=kind)
    session.add(command)
    session.flush()
    session.expire_all()  # the endpoint reads `kind` back as a plain string

    resp = client.post(
        f"/api/bots/{bot.slug}/commands/{command.id}/ack",
        json={"result": "ok", "detail": "done"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert getattr(bot, field) is after
    assert command.acked_ts is not None
    assert (command.result, command.result_detail) == ("ok", "done")
    assert len(_events(session, bot, EventKind.COMMAND)) == 1


def test_a_failed_ack_does_not_flip_the_flag(client, session, bot_factory):
    bot, token = bot_factory()
    command = BotCommand(bot_id=bot.id, kind=CommandKind.PAUSE)
    session.add(command)
    session.flush()

    resp = client.post(
        f"/api/bots/{bot.slug}/commands/{command.id}/ack",
        json={"result": "error", "detail": "no connection"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert bot.paused_entries is False
    assert command.result == "error"
    assert len(_events(session, bot, EventKind.COMMAND)) == 1


def test_acking_another_bots_command_is_404(client, session, bot_factory):
    one, _ = bot_factory(name="one")
    _, two_token = bot_factory(name="two")
    command = BotCommand(bot_id=one.id, kind=CommandKind.PAUSE)
    session.add(command)
    session.flush()

    resp = client.post(
        f"/api/bots/two/commands/{command.id}/ack",
        json={"result": "ok"},
        headers=_auth(two_token),
    )
    assert resp.status_code == 404


# --- journal stamping -------------------------------------------------------


def _plan_payload(account_id, instrument_id):
    return {
        "account_id": account_id,
        "instrument_id": instrument_id,
        "direction": "long",
        "planned_entry": "60000",
        "planned_stop": "57000",
        "risk_eur": "50",
        "note_pre": "donchian breakout",
    }


def test_a_trade_planned_under_bot_auth_is_stamped(
    client, session, bot_factory, instrument_factory
):
    bot, token = bot_factory()
    instrument = instrument_factory()
    resp = client.post(
        "/api/trades", json=_plan_payload(bot.account_id, instrument.id), headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    trade = resp.json()
    assert trade["bot_id"] == bot.id

    listed = client.get("/api/trades", headers=_auth(token)).json()
    assert [one["id"] for one in listed["items"]] == [trade["id"]]

    filtered = client.get(f"/api/trades?mode=all&bot_id={bot.id}").json()
    assert [one["id"] for one in filtered["items"]] == [trade["id"]]


def test_a_bot_cannot_journal_into_a_foreign_account(
    client, bot_factory, account_factory, instrument_factory
):
    _, token = bot_factory()
    other = account_factory()
    resp = client.post(
        "/api/trades", json=_plan_payload(other.id, instrument_factory().id), headers=_auth(token)
    )
    assert resp.status_code == 403


def test_a_bot_cannot_touch_another_bots_trade(client, bot_factory, instrument_factory):
    one, one_token = bot_factory(name="one")
    _, two_token = bot_factory(name="two")
    trade = client.post(
        "/api/trades",
        json=_plan_payload(one.account_id, instrument_factory().id),
        headers=_auth(one_token),
    ).json()

    fills = {"manual": {"ts": "2026-09-09T12:00:00+00:00", "quantity": "1", "price": "60000"}}
    assert (
        client.post(f"/api/trades/{trade['id']}/open", json=fills, headers=_auth(two_token))
    ).status_code == 403
    # The local UI (no token) is unchanged.
    opened = client.post(f"/api/trades/{trade['id']}/open", json=fills)
    assert opened.status_code == 200, opened.text
    assert opened.json()["status"] == TradeStatus.OPEN


def test_a_bot_cannot_read_or_attach_to_another_bots_trade(
    client, bot_factory, instrument_factory
):
    one, one_token = bot_factory(name="one")
    _, two_token = bot_factory(name="two")
    trade = client.post(
        "/api/trades",
        json=_plan_payload(one.account_id, instrument_factory().id),
        headers=_auth(one_token),
    ).json()

    read = client.get(f"/api/trades/{trade['id']}", headers=_auth(two_token))
    assert read.status_code == 403
    write = client.post(
        f"/api/trades/{trade['id']}/screenshots",
        files={"files": ("shot.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        headers=_auth(two_token),
    )
    assert write.status_code == 403
    # The local UI still sees it.
    assert client.get(f"/api/trades/{trade['id']}").status_code == 200


def test_a_bot_cannot_edit_its_trade_into_a_foreign_account(
    client, bot_factory, account_factory, instrument_factory
):
    bot, token = bot_factory()
    instrument = instrument_factory()
    trade = client.post(
        "/api/trades", json=_plan_payload(bot.account_id, instrument.id), headers=_auth(token)
    ).json()

    payload = _plan_payload(account_factory().id, instrument.id)
    foreign = client.put(f"/api/trades/{trade['id']}", json=payload, headers=_auth(token))
    assert foreign.status_code == 403

    payload["account_id"] = bot.account_id
    own = client.put(f"/api/trades/{trade['id']}", json=payload, headers=_auth(token))
    assert own.status_code == 200, own.text


def test_an_unreadable_stage_capital_setting_is_a_clear_500(
    client, session, bot_factory, account_factory
):
    account = account_factory(mode=Mode.LIVE)
    session.add(Setting(key="stage_capital_live", value="zweihundert"))
    bot, token = bot_factory(account_id=account.id)
    session.flush()

    resp = client.get(f"/api/bots/{bot.slug}/config", headers=_auth(token))
    assert resp.status_code == 500
    assert resp.json()["detail"] == "invalid stage_capital setting: stage_capital_live"
