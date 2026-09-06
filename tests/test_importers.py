from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from trade_ledger.enums import TxSource, TxType
from trade_ledger.importers import IMPORTERS
from trade_ledger.ledger import upsert_transactions

FIXTURES = Path(__file__).parent / "fixtures" / "csv"


def _load(name: str) -> bytes:
    return (FIXTURES / f"{name}.csv").read_bytes()


def test_trading212_importer():
    result = IMPORTERS["trading212"](_load("trading212"))
    assert len(result.drafts) == 7
    assert len(result.errors) == 1
    assert result.errors[0].row == 8  # the "not-a-number" Total row

    buy = next(d for d in result.drafts if d.external_id == "T212-001")
    assert buy.type == TxType.BUY
    assert buy.quantity == Decimal("1.5")
    assert buy.amount_eur == Decimal("204.55")
    assert buy.fee_eur == Decimal("0.45")
    assert buy.source == TxSource.CSV

    fee_row = next(d for d in result.drafts if d.external_id == "T212-008")
    assert fee_row.type == TxType.FEE
    assert fee_row.fee_eur == Decimal("0.60")
    assert fee_row.amount_eur == Decimal(0)


def test_okx_importer():
    result = IMPORTERS["okx"](_load("okx"))
    assert len(result.drafts) == 3
    assert len(result.errors) == 1
    assert result.errors[0].row == 5  # Side "hold" is unrecognised

    buy = next(d for d in result.drafts if d.external_id == "OKX-TRADE-1")
    assert buy.type == TxType.BUY
    assert buy.quantity == Decimal("0.10")
    assert buy.amount_eur == Decimal("4200.0000")
    assert buy.fee_eur == Decimal("4.20")
    assert buy.instrument_symbol == "BTC"

    pending = next(d for d in result.drafts if d.external_id == "OKX-TRADE-3")
    assert pending.fx_source == "pending"
    assert pending.amount_eur == Decimal(0)


def test_kraken_importer():
    result = IMPORTERS["kraken"](_load("kraken"))
    assert len(result.drafts) == 5
    assert len(result.errors) == 1
    assert result.errors[0].row == 8  # REF6's unpaired trade leg

    buy = next(d for d in result.drafts if d.external_id == "REF1")
    assert buy.type == TxType.BUY
    assert buy.quantity == Decimal("0.1000000000")
    assert buy.amount_eur == Decimal("4200.0000")
    assert buy.fee_eur == Decimal("4.2000000000")
    assert buy.instrument_symbol == "BTC"  # XXBT normalised

    reward = next(d for d in result.drafts if d.external_id == "REF5")
    assert reward.type == TxType.STAKING_REWARD
    assert reward.fx_source == "pending"
    assert reward.instrument_symbol == "ETH"  # XETH normalised


def test_binance_importer():
    result = IMPORTERS["binance"](_load("binance"))
    assert len(result.drafts) == 5
    assert len(result.errors) == 1
    assert result.errors[0].row == 9  # Operation "Foo" is unrecognised

    buy = next(d for d in result.drafts if d.type == TxType.BUY)
    assert buy.instrument_symbol == "BTC"
    assert buy.quantity == Decimal("0.10000000")
    assert buy.amount_eur == Decimal("4300.00000000")
    assert buy.fee_eur == Decimal("2.15000000")
    assert buy.external_id is None  # no natural id; upsert_transactions hashes it

    airdrop = next(d for d in result.drafts if d.type == TxType.AIRDROP)
    assert airdrop.instrument_symbol == "BNB"
    assert airdrop.fx_source == "pending"


def test_generic_importer():
    result = IMPORTERS["generic"](_load("generic"))
    assert len(result.drafts) == 5
    assert len(result.errors) == 1
    assert result.errors[0].row == 7  # type "not_a_type" isn't a TxType

    buy = next(d for d in result.drafts if d.external_id == "GEN-001")
    assert buy.type == TxType.BUY
    assert buy.quantity == Decimal("0.2")
    assert buy.amount_eur == Decimal(8000)
    assert buy.fee_eur == Decimal(5)


def test_committing_an_import_twice_adds_nothing_the_second_time(session, account_factory):
    account = account_factory()
    result = IMPORTERS["okx"](_load("okx"))

    added, skipped = upsert_transactions(session, account, result.drafts)
    assert (added, skipped) == (3, 0)

    added2, skipped2 = upsert_transactions(session, account, result.drafts)
    assert added2 == 0
    assert skipped2 == 3


def _create_account(client, **overrides):
    payload = {"venue": "okx", "name": "okx-import", "kind": "crypto_spot", "mode": "paper"}
    payload.update(overrides)
    resp = client.post("/api/accounts", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_preview_endpoint_returns_drafts_errors_and_duplicate_count(client):
    account = _create_account(client)
    resp = client.post(
        f"/api/imports/preview?format=okx&account_id={account['id']}",
        content=_load("okx"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["drafts"]) == 3
    assert len(body["errors"]) == 1
    assert body["duplicates"] == 0
    assert body["drafts"][0]["amount_eur"] == "4200.0000"  # Money serialises as a string


def test_preview_endpoint_rejects_unknown_format(client):
    account = _create_account(client)
    resp = client.post(
        f"/api/imports/preview?format=nope&account_id={account['id']}",
        content=b"",
    )
    assert resp.status_code == 422


def test_commit_endpoint_is_idempotent_and_writes_a_sync_run(client):
    account = _create_account(client)
    preview = client.post(
        f"/api/imports/preview?format=okx&account_id={account['id']}",
        content=_load("okx"),
    ).json()

    first = client.post(
        "/api/imports/commit",
        json={"account_id": account["id"], "drafts": preview["drafts"]},
    )
    assert first.status_code == 200, first.text
    assert first.json() == {"added": 3, "skipped": 0}

    second = client.post(
        "/api/imports/commit",
        json={"account_id": account["id"], "drafts": preview["drafts"]},
    )
    assert second.json() == {"added": 0, "skipped": 3}

    tx_count = client.get("/api/transactions", params={"account_id": account["id"]}).json()
    assert tx_count["total"] == 3
