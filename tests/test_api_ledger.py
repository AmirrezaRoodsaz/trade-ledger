from __future__ import annotations


def _create_account(client, **overrides):
    payload = {
        "venue": "okx",
        "name": "okx-main",
        "kind": "crypto_spot",
        "mode": "paper",
    }
    payload.update(overrides)
    resp = client.post("/api/accounts", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_instrument(client, **overrides):
    payload = {"symbol": "BTC", "asset_class": "crypto"}
    payload.update(overrides)
    resp = client.post("/api/instruments", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_account(client):
    account = _create_account(client)
    assert account["name"] == "okx-main"
    assert account["tax_wallet"] == "okx-main"
    assert account["active"] is True


def test_manual_buy_shows_up_in_summary(client):
    account = _create_account(client)
    instrument = _create_instrument(client)

    resp = client.post(
        "/api/transactions",
        json={
            "account_id": account["id"],
            "ts": "2026-01-01T00:00:00+00:00",
            "type": "buy",
            "instrument_id": instrument["id"],
            "quantity": "0.5",
            "price": "40000",
            "amount_eur": "20000",
            "fee_eur": "12",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source"] == "manual"
    assert body["amount_eur"] == "20000"

    summary = client.get(f"/api/accounts/{account['id']}/summary").json()
    assert summary["cash_eur"] == "-20012"
    assert summary["tx_count"] == 1
    assert summary["positions"] == [{"instrument": "BTC", "quantity": "0.5"}]
    assert summary["last_sync"] is None


def test_delete_account_with_transactions_conflicts(client):
    account = _create_account(client)
    client.post(
        "/api/transactions",
        json={
            "account_id": account["id"],
            "ts": "2026-01-01T00:00:00+00:00",
            "type": "deposit",
            "instrument_id": None,
            "amount_eur": "100",
        },
    )

    resp = client.delete(f"/api/accounts/{account['id']}")
    assert resp.status_code == 409


def test_delete_account_without_transactions_succeeds(client):
    account = _create_account(client, name="empty-account")
    resp = client.delete(f"/api/accounts/{account['id']}")
    assert resp.status_code == 204


def test_env_status_returns_booleans_only(client):
    resp = client.get("/api/settings/env-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data  # non-empty: parsed from .env.example
    assert all(isinstance(value, bool) for value in data.values())


def test_transactions_list_is_paginated_and_filterable(client):
    account = _create_account(client)
    for i in range(3):
        client.post(
            "/api/transactions",
            json={
                "account_id": account["id"],
                "ts": f"2026-01-0{i + 1}T00:00:00+00:00",
                "type": "deposit",
                "amount_eur": "10",
            },
        )

    resp = client.get("/api/transactions", params={"account_id": account["id"], "page_size": 2})
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


def test_transactions_export_csv_streams_all_columns(client):
    account = _create_account(client)
    client.post(
        "/api/transactions",
        json={
            "account_id": account["id"],
            "ts": "2026-01-01T00:00:00+00:00",
            "type": "deposit",
            "amount_eur": "10",
        },
    )
    resp = client.get("/api/transactions/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0].split(",")[0] == "id"
    assert len(lines) == 2


def test_account_delete_missing_returns_404(client):
    resp = client.delete("/api/accounts/999")
    assert resp.status_code == 404


def test_put_account_without_tax_wallet_defaults_to_name(client):
    account = _create_account(client, name="okx-main")

    resp = client.put(
        f"/api/accounts/{account['id']}",
        json={
            "venue": "okx",
            "name": "okx-main",
            "kind": "crypto_spot",
            "mode": "live",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "live"
    assert body["tax_wallet"] == "okx-main"


def test_post_transaction_buy_with_negative_amount_eur_is_rejected(client):
    account = _create_account(client)
    resp = client.post(
        "/api/transactions",
        json={
            "account_id": account["id"],
            "ts": "2026-01-01T00:00:00+00:00",
            "type": "buy",
            "amount_eur": "-500",
        },
    )
    assert resp.status_code == 422


def test_post_transaction_adjustment_allows_negative_amount_eur(client):
    account = _create_account(client)
    resp = client.post(
        "/api/transactions",
        json={
            "account_id": account["id"],
            "ts": "2026-01-01T00:00:00+00:00",
            "type": "adjustment",
            "amount_eur": "-5",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["amount_eur"] == "-5"


def test_put_instrument_updates_fields(client):
    instrument = _create_instrument(client)
    resp = client.put(
        f"/api/instruments/{instrument['id']}",
        json={"symbol": "BTC", "asset_class": "crypto", "name": "Bitcoin"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Bitcoin"


def test_post_duplicate_instrument_is_rejected(client):
    _create_instrument(client)
    resp = client.post("/api/instruments", json={"symbol": "BTC", "asset_class": "crypto"})
    assert resp.status_code == 422
