from __future__ import annotations

from datetime import UTC, datetime

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x01\x02\xff\xfe"


def _plan(client, account, instrument, **overrides):
    payload = {
        "account_id": account.id,
        "instrument_id": instrument.id,
        "direction": "long",
        "planned_entry": "100",
        "planned_stop": "95",
        "risk_eur": "50",
        "note_pre": "range breakout",
        "tags": ["breakout"],
    }
    payload.update(overrides)
    return client.post("/api/trades", json=payload)


def _manual(ts, quantity="10", price="100", fee="1"):
    return {"manual": {"ts": ts, "quantity": quantity, "price": price, "fee_eur": fee}}


def test_plan_without_stop_is_422(client, account_factory, instrument_factory):
    resp = _plan(client, account_factory(), instrument_factory(), planned_stop=None)
    assert resp.status_code == 422, resp.text
    assert "planned_stop" in resp.json()["detail"]


def test_plan_open_close_review(client, account_factory, instrument_factory):
    trade = _plan(client, account_factory(), instrument_factory()).json()
    assert trade["status"] == "planned"
    assert trade["external_ref"] == "T-001"
    assert trade["tags"] == ["breakout"]

    opened = client.post(
        f"/api/trades/{trade['id']}/open", json=_manual("2026-03-02T10:00:00+00:00")
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["status"] == "open"
    assert opened.json()["avg_entry"] == "100"

    closed = client.post(
        f"/api/trades/{trade['id']}/close",
        json=_manual("2026-03-03T10:00:00+00:00", price="120"),
    ).json()
    assert closed["status"] == "closed"
    assert closed["result_eur"] == "198"
    assert closed["r_multiple"] == "3.96"

    reviewed = client.post(
        f"/api/trades/{trade['id']}/review",
        json={"adherence": True, "mistake": "early_exit", "note_post": "took the target"},
    ).json()
    assert reviewed["adherence"] is True
    assert reviewed["mistake"] == "early_exit"


def test_update_trade_keeps_the_journal_id(client, account_factory, instrument_factory):
    account, instrument = account_factory(), instrument_factory()
    trade = _plan(client, account, instrument).json()
    updated = client.put(
        f"/api/trades/{trade['id']}",
        json={
            "account_id": account.id,
            "instrument_id": instrument.id,
            "direction": "long",
            "planned_entry": "100",
            "planned_stop": "96",
            "risk_eur": "40",
            "note_pre": "tightened the stop",
        },
    ).json()
    assert updated["planned_stop"] == "96"
    assert updated["external_ref"] == trade["external_ref"]
    assert updated["tags"] == []

    same_stop = client.put(
        f"/api/trades/{trade['id']}",
        json={
            "account_id": account.id,
            "instrument_id": instrument.id,
            "direction": "long",
            "planned_entry": "100",
            "planned_stop": "100",
        },
    )
    assert same_stop.status_code == 422


def test_open_an_open_trade_is_409(client, account_factory, instrument_factory):
    trade = _plan(client, account_factory(), instrument_factory()).json()
    client.post(f"/api/trades/{trade['id']}/open", json=_manual("2026-03-02T10:00:00+00:00"))
    resp = client.post(f"/api/trades/{trade['id']}/open", json=_manual("2026-03-02T11:00:00+00:00"))
    assert resp.status_code == 409, resp.text


def test_cancel_and_delete(client, account_factory, instrument_factory):
    account, instrument = account_factory(), instrument_factory()
    trade = _plan(client, account, instrument).json()
    assert client.post(f"/api/trades/{trade['id']}/cancel").json()["status"] == "cancelled"
    assert client.delete(f"/api/trades/{trade['id']}").status_code == 204
    assert client.get(f"/api/trades/{trade['id']}").status_code == 404

    open_trade = _plan(client, account, instrument).json()
    client.post(f"/api/trades/{open_trade['id']}/open", json=_manual("2026-03-02T10:00:00+00:00"))
    assert client.delete(f"/api/trades/{open_trade['id']}").status_code == 409


def test_list_filters_by_mode_status_and_tag(client, account_factory, instrument_factory):
    paper, live = account_factory(), account_factory(mode="live")
    instrument = instrument_factory()
    _plan(client, paper, instrument)
    _plan(client, live, instrument, tags=["trend"])

    assert client.get("/api/trades").json()["total"] == 1  # default mode=paper
    assert client.get("/api/trades", params={"mode": "all"}).json()["total"] == 2
    assert client.get("/api/trades", params={"mode": "live", "tag": "trend"}).json()["total"] == 1
    assert (
        client.get("/api/trades", params={"mode": "live", "tag": "breakout"}).json()["total"] == 0
    )
    assert client.get("/api/trades", params={"mode": "all", "status": "open"}).json()["total"] == 0
    assert client.get("/api/trades", params={"mode": "nonsense"}).status_code == 422


def test_suggest_fills_endpoint(client, account_factory, instrument_factory):
    account, instrument = account_factory(), instrument_factory()
    trade = _plan(client, account, instrument).json()
    # A planned trade has no timestamp yet, so `suggest_fills` looks around
    # "now" — the fill has to be recent to be a candidate.
    client.post(
        "/api/transactions",
        json={
            "account_id": account.id,
            "ts": datetime.now(UTC).isoformat(),
            "type": "buy",
            "instrument_id": instrument.id,
            "quantity": "10",
            "amount_eur": "1000",
        },
    )
    suggested = client.get(f"/api/trades/{trade['id']}/suggest-fills").json()
    assert len(suggested) == 1

    opened = client.post(
        f"/api/trades/{trade['id']}/open", json={"fill_ids": [suggested[0]["id"]]}
    ).json()
    assert opened["avg_entry"] == "100"
    assert client.get(f"/api/trades/{trade['id']}/suggest-fills").json() == []


def test_calendar_sums_by_day(client, account_factory, instrument_factory):
    account, instrument = account_factory(), instrument_factory()
    for close_ts, price in (
        ("2026-03-03T10:00:00+00:00", "120"),
        ("2026-03-03T15:00:00+00:00", "110"),
        ("2026-03-04T15:00:00+00:00", "90"),
    ):
        trade = _plan(client, account, instrument).json()
        client.post(f"/api/trades/{trade['id']}/open", json=_manual("2026-03-02T10:00:00+00:00"))
        client.post(f"/api/trades/{trade['id']}/close", json=_manual(close_ts, price=price))

    days = client.get("/api/trades/calendar", params={"year": 2026, "month": 3}).json()
    assert days["2026-03-03"] == {"count": 2, "result_eur": "296", "r": "5.92"}
    assert days["2026-03-04"] == {"count": 1, "result_eur": "-102", "r": "-2.04"}
    assert client.get("/api/trades/calendar", params={"year": 2026, "month": 4}).json() == {}


def test_screenshot_upload(client, account_factory, instrument_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    trade = _plan(client, account_factory(), instrument_factory()).json()

    resp = client.post(
        f"/api/trades/{trade['id']}/screenshots",
        files={"file": ("entry chart.png", PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 200, resp.text
    stored = resp.json()["screenshots"]
    assert len(stored) == 1
    assert stored[0].startswith(f"screenshots/{trade['id']}/")
    assert stored[0].endswith("-entry_chart.png")
    assert (tmp_path / stored[0]).read_bytes() == PNG_BYTES

    bad = client.post(
        f"/api/trades/{trade['id']}/screenshots",
        files={"file": ("notes.pdf", b"%PDF", "application/pdf")},
    )
    assert bad.status_code == 422


def test_playbooks_versions_tags_and_daily_notes(client):
    playbook = client.post("/api/playbooks", json={"name": "Turtle Donchian"}).json()
    assert client.post("/api/playbooks", json={"name": "Turtle Donchian"}).status_code == 409

    first = client.post(
        f"/api/playbooks/{playbook['id']}/versions", json={"rules_md": "# v1"}
    ).json()
    second = client.post(
        f"/api/playbooks/{playbook['id']}/versions", json={"rules_md": "# v2"}
    ).json()
    assert (first["version"], second["version"]) == (1, 2)
    assert len(client.get(f"/api/playbooks/{playbook['id']}/versions").json()) == 2
    assert client.get("/api/playbooks/999/versions").status_code == 404

    tag = client.post("/api/tags", json={"kind": "setup", "name": "breakout"}).json()
    assert client.post("/api/tags", json={"kind": "setup", "name": "breakout"}).status_code == 409
    assert client.get("/api/tags", params={"kind": "setup"}).json() == [tag]
    assert client.delete(f"/api/tags/{tag['id']}").status_code == 204
    assert client.get("/api/tags").json() == []

    assert client.get("/api/daily-notes/2026-03-02").status_code == 404
    note = client.put(
        "/api/daily-notes/2026-03-02", json={"text": "flat day", "hours_spent": "1.5"}
    ).json()
    assert note["hours_spent"] == "1.5"
    updated = client.put("/api/daily-notes/2026-03-02", json={"text": "revised"}).json()
    assert (updated["id"], updated["text"]) == (note["id"], "revised")
    assert client.get("/api/daily-notes/2026-03-02").json()["text"] == "revised"

    client.put("/api/daily-notes/2026-04-01", json={"text": "later"})
    ranged = client.get("/api/daily-notes", params={"from": "2026-03-01", "to": "2026-03-31"})
    assert [n["date"] for n in ranged.json()] == ["2026-03-02"]


def test_trades_filter_by_playbook(client, account_factory, instrument_factory):
    playbook = client.post("/api/playbooks", json={"name": "Donchian"}).json()
    version = client.post(
        f"/api/playbooks/{playbook['id']}/versions", json={"rules_md": "# v1"}
    ).json()
    account, instrument = account_factory(), instrument_factory()
    _plan(client, account, instrument, playbook_version_id=version["id"])
    _plan(client, account, instrument)

    assert client.get("/api/trades", params={"playbook_id": playbook["id"]}).json()["total"] == 1
    assert client.get("/api/trades", params={"playbook_id": 999}).json()["total"] == 0
