from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from trade_ledger.enums import TxSource, TxType
from trade_ledger.importers import IMPORTERS
from trade_ledger.ledger import cash_delta_eur, upsert_transactions

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
    assert fee_row.amount_eur == Decimal("0.60")  # FEE's cash effect reads amount_eur
    assert fee_row.fee_eur == Decimal(0)
    assert cash_delta_eur(fee_row) == Decimal("-0.60")


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
    # fee is already EUR: don't also keep it in fee/fee_ccy (matches the other importers)
    assert buy.fee == Decimal(0)
    assert buy.fee_ccy is None

    pending = next(d for d in result.drafts if d.external_id == "OKX-TRADE-3")
    assert pending.fx_source == "pending"
    assert pending.amount_eur == Decimal(0)


def test_kraken_importer():
    result = IMPORTERS["kraken"](_load("kraken"))
    assert len(result.drafts) == 6
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

    # REF7: EUR fiat leg with zero fee, BTC asset leg with a nonzero fee —
    # the asset-leg fee can't be expressed in EUR, so it goes to fee/fee_ccy
    # pending rather than being summed into fee_eur.
    asset_fee_trade = next(d for d in result.drafts if d.external_id == "REF7")
    assert asset_fee_trade.fee_eur == Decimal(0)
    assert asset_fee_trade.fee == Decimal("0.0001000000")
    assert asset_fee_trade.fee_ccy == "BTC"
    assert asset_fee_trade.fx_source == "pending"


def test_binance_importer():
    result = IMPORTERS["binance"](_load("binance"))
    assert len(result.drafts) == 6
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

    # A `Fee` row with no Buy/Sell leg at that timestamp used to be silently
    # dropped; it must now surface as its own TxType.FEE draft.
    fee_only = [d for d in result.drafts if d.type == TxType.FEE]
    assert len(fee_only) == 1
    assert fee_only[0].amount_eur == Decimal("1.00000000")
    assert fee_only[0].fee_eur == Decimal(0)


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
        "/api/imports/preview",
        files={"file": ("okx.csv", _load("okx"), "text/csv")},
        data={"format": "okx", "account_id": account["id"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["drafts"]) == 3
    assert len(body["errors"]) == 1
    assert body["duplicates"] == 0
    assert body["drafts"][0]["amount_eur"] == "4200.0000"  # Money serialises as a string


def test_preview_endpoint_reports_duplicates_already_in_the_db(client):
    account = _create_account(client)
    files = {"file": ("okx.csv", _load("okx"), "text/csv")}
    data = {"format": "okx", "account_id": account["id"]}

    first_preview = client.post("/api/imports/preview", files=files, data=data).json()
    client.post(
        "/api/imports/commit",
        json={"account_id": account["id"], "drafts": first_preview["drafts"]},
    )

    second_preview = client.post("/api/imports/preview", files=files, data=data).json()
    assert second_preview["duplicates"] == 3


def test_preview_endpoint_rejects_unknown_format(client):
    account = _create_account(client)
    resp = client.post(
        "/api/imports/preview",
        files={"file": ("empty.csv", b"", "text/csv")},
        data={"format": "nope", "account_id": account["id"]},
    )
    assert resp.status_code == 422


def test_commit_endpoint_is_idempotent_and_writes_a_sync_run(client):
    account = _create_account(client)
    preview = client.post(
        "/api/imports/preview",
        files={"file": ("okx.csv", _load("okx"), "text/csv")},
        data={"format": "okx", "account_id": account["id"]},
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
