from __future__ import annotations


def _plan(client, account, instrument, **overrides):
    payload = {
        "account_id": account.id,
        "instrument_id": instrument.id,
        "direction": "long",
        "planned_entry": "100",
        "planned_stop": "95",
        "risk_eur": "50",
        "note_pre": "range breakout",
    }
    payload.update(overrides)
    resp = client.post("/api/trades", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_export_then_import_round_trip(tmp_path, client, account_factory, instrument_factory):
    account = account_factory()
    instrument = instrument_factory(symbol="BTC")
    trade = _plan(client, account, instrument)

    export = client.post("/api/notes/export", json={"dir": str(tmp_path)})
    assert export.status_code == 200, export.text
    assert export.json() == {"files": 1}
    assert list(tmp_path.glob("*.md"))

    # Edit the note on disk the way a human would in Obsidian, then import it back.
    note_path = next(tmp_path.glob("*.md"))
    note_path.write_text(note_path.read_text().replace("status: planned", "status: cancelled"))

    imp = client.post(
        "/api/notes/import", json={"dir": str(tmp_path), "account_id": account.id}
    )
    assert imp.status_code == 200, imp.text
    assert imp.json() == {"created": 0, "updated": 1}

    refreshed = client.get(f"/api/trades/{trade['id']}").json()
    assert refreshed["status"] == "cancelled"


def test_import_404s_for_unknown_account(tmp_path, client):
    resp = client.post("/api/notes/import", json={"dir": str(tmp_path), "account_id": 999})
    assert resp.status_code == 404
