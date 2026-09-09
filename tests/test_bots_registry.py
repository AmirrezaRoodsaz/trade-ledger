from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from trade_ledger import db
from trade_ledger.api.bots import slugify
from trade_ledger.bots import auth
from trade_ledger.bots.schedule import deadline, next_run
from trade_ledger.models import BotRun, Setting


def _create_account(client, name="okx-bot-account"):
    resp = client.post(
        "/api/accounts",
        json={"venue": "okx", "name": name, "kind": "crypto_spot", "mode": "paper"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_bot(client, **overrides):
    account = overrides.pop("account", None) or _create_account(client)
    payload = {
        "name": "OKX Donchian 4h",
        "account_id": account["id"],
        "strategy": "donchian",
        "host": "local",
        "schedule_every_s": 14400,
        "schedule_at": "00:05",
    }
    payload.update(overrides)
    resp = client.post("/api/bots", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request({"type": "http", "headers": headers})


# --- registry ---------------------------------------------------------------


def test_create_bot_returns_token_once_and_never_the_hash(client, session):
    body = _create_bot(client)
    bot, token = body["bot"], body["token"]

    assert bot["slug"] == "okx-donchian-4h"
    assert bot["status"] == "ok"
    assert bot["grace_s"] == 3300
    assert "token_hash" not in bot
    assert auth.verify(session, token).slug == "okx-donchian-4h"

    listed = client.get("/api/bots").json()
    assert [one["slug"] for one in listed] == ["okx-donchian-4h"]
    assert "token" not in listed[0] and "token_hash" not in listed[0]

    fetched = client.get("/api/bots/okx-donchian-4h").json()
    assert fetched == bot


def test_wrong_token_verifies_to_none(client, session):
    _create_bot(client)
    assert auth.verify(session, "not-a-token") is None
    assert auth.verify(session, "") is None


def test_rotate_invalidates_the_old_token(client, session):
    old = _create_bot(client)["token"]
    new = client.post("/api/bots/okx-donchian-4h/token").json()["token"]

    assert new != old
    assert auth.verify(session, old) is None
    assert auth.verify(session, new).slug == "okx-donchian-4h"


def test_duplicate_slug_conflicts(client):
    account = _create_account(client)
    _create_bot(client, account=account)
    resp = client.post(
        "/api/bots",
        json={
            "name": "okx donchian 4h",  # same slug, different spelling
            "account_id": account["id"],
            "strategy": "donchian",
        },
    )
    assert resp.status_code == 409


def test_slugify_strips_to_url_safe():
    assert slugify("OKX Donchian 4h") == "okx-donchian-4h"
    assert slugify("Bot #1 (paper!)") == "bot-1-paper"


def test_create_bot_rejects_unknown_account_and_bad_schedule(client):
    resp = client.post(
        "/api/bots", json={"name": "ghost", "account_id": 999, "strategy": "donchian"}
    )
    assert resp.status_code == 404

    account = _create_account(client)
    resp = client.post(
        "/api/bots",
        json={
            "name": "ghost",
            "account_id": account["id"],
            "strategy": "donchian",
            "schedule_at": "25:00",
        },
    )
    assert resp.status_code == 422


def test_update_bot_keeps_the_slug(client):
    account = _create_account(client)
    _create_bot(client, account=account)
    resp = client.put(
        "/api/bots/okx-donchian-4h",
        json={
            "name": "Renamed",
            "account_id": account["id"],
            "strategy": "donchian",
            "schedule_every_s": 3600,
            "schedule_at": "00:10",
            "dry_run": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slug"] == "okx-donchian-4h"
    assert (body["name"], body["schedule_every_s"], body["dry_run"]) == ("Renamed", 3600, True)


def test_delete_bot_without_history_succeeds(client):
    _create_bot(client)
    assert client.delete("/api/bots/okx-donchian-4h").status_code == 204
    assert client.get("/api/bots/okx-donchian-4h").status_code == 404


def test_delete_bot_with_runs_conflicts(client, session):
    bot = _create_bot(client)["bot"]
    session.add(BotRun(bot_id=bot["id"]))
    session.commit()

    assert client.delete("/api/bots/okx-donchian-4h").status_code == 409


def test_env_status_is_booleans_for_the_known_keys(client, tmp_path, monkeypatch):
    _create_bot(client)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    env_file = tmp_path / "bots" / "okx-donchian-4h" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("# bot env\nBOT_TOKEN=abc\nEXCHANGE_KEY=\n")

    data = client.get("/api/bots/okx-donchian-4h/env-status").json()
    assert all(isinstance(value, bool) for value in data.values())
    assert data["BOT_TOKEN"] is True
    assert data["EXCHANGE_KEY"] is False
    assert data["EXCHANGE_SECRET"] is False
    assert "abc" not in str(data)


def test_unknown_bot_is_404(client):
    for path in ("", "/env-status"):
        assert client.get(f"/api/bots/nope{path}").status_code == 404
    assert client.post("/api/bots/nope/token").status_code == 404
    assert client.delete("/api/bots/nope").status_code == 404


# --- auth dependency --------------------------------------------------------


def test_bot_auth_without_header_is_the_local_ui(session, bot_factory):
    bot_factory()
    assert auth.bot_auth(_request([]), session) is None


def test_bot_auth_accepts_a_good_bearer_and_401s_a_bad_one(session, bot_factory):
    bot, token = bot_factory()
    good = _request([(b"authorization", f"Bearer {token}".encode())])
    assert auth.bot_auth(good, session).id == bot.id

    bad = _request([(b"authorization", b"Bearer nonsense")])
    with pytest.raises(HTTPException) as excinfo:
        auth.bot_auth(bad, session)
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "invalid bot token"


# --- schedule ---------------------------------------------------------------


def test_next_run_walks_the_anchor_forward():
    now = datetime(2026, 9, 9, 10, 0, tzinfo=UTC)
    assert next_run(14400, "00:05", now) == datetime(2026, 9, 9, 12, 5, tzinfo=UTC)


def test_next_run_on_the_anchor_returns_the_next_slot():
    anchor = datetime(2026, 9, 9, 0, 5, tzinfo=UTC)
    assert next_run(14400, "00:05", anchor) == datetime(2026, 9, 9, 4, 5, tzinfo=UTC)


def test_next_run_before_the_anchor_walks_back_over_midnight():
    now = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
    # slots ... 20:05 (8th), 00:05 (9th) — the first one after 00:00 is 00:05.
    assert next_run(14400, "00:05", now) == datetime(2026, 9, 9, 0, 5, tzinfo=UTC)


def test_next_run_rejects_a_non_positive_interval():
    with pytest.raises(ValueError):
        next_run(0, "00:05", datetime(2026, 9, 9, tzinfo=UTC))


def test_deadline_counts_from_the_last_heartbeat_else_creation(bot_factory):
    created = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
    bot, _ = bot_factory(created=created, schedule_every_s=14400, grace_s=3300)
    assert deadline(bot) == created + timedelta(seconds=14400 + 3300)

    bot.last_heartbeat = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    assert deadline(bot) == bot.last_heartbeat + timedelta(seconds=14400 + 3300)


# --- migration --------------------------------------------------------------


def test_migration_adds_bot_id_to_an_older_trades_table(tmp_path):
    path = tmp_path / "ledger.db"
    # A pre-bot database: `trades` as it was before the column existed.
    # ponytail: a hand-written subset rather than a copy of the old metadata —
    # `create_all` leaves an existing table alone, so the columns it lacks are
    # exactly what the migration has to notice.
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL,"
            " instrument_id INTEGER NOT NULL, direction VARCHAR NOT NULL, status VARCHAR)"
        )
        assert "bot_id" not in {row[1] for row in conn.execute("PRAGMA table_info(trades)")}

    db.init_db(path)

    with db.engine.begin() as conn:
        assert "bot_id" in _trade_columns(conn)
    with db.SessionLocal() as session:
        assert session.get(Setting, "schema_version").value == "2"


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "ledger.db"
    db.init_db(path)
    db.init_db(path)  # would raise "duplicate column name" without the check
    with db.engine.begin() as conn:
        assert "bot_id" in _trade_columns(conn)


def _trade_columns(conn) -> set[str]:
    return {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(trades)")}
