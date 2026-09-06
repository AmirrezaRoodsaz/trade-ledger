from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from trade_ledger.adapters import base as adapters_base
from trade_ledger.adapters.ccxt_adapter import CcxtAdapter
from trade_ledger.enums import TxSource, TxType, Venue
from trade_ledger.ledger import TxDraft
from trade_ledger.models import Transaction
from trade_ledger.settings import Settings


class _StubAdapter:
    """Fake `Adapter` for testing `sync_account`'s bookkeeping without HTTP."""

    def __init__(self, drafts: list[TxDraft]):
        self._drafts = drafts
        self.since_calls: list[datetime | None] = []
        self.closed = False

    def fetch_transactions(self, since):
        self.since_calls.append(since)
        return self._drafts

    def fetch_balances(self):
        return {}

    def close(self):
        self.closed = True


def _deposit_draft(external_id: str, amount: str = "100") -> TxDraft:
    return TxDraft(
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        type=TxType.DEPOSIT,
        amount_eur=Decimal(amount),
        external_id=external_id,
        source=TxSource.API,
    )


def test_sync_account_missing_credentials_sets_error(session, account_factory, monkeypatch):
    monkeypatch.delenv("T212_TEST_API_KEY", raising=False)
    monkeypatch.delenv("T212_TEST_API_SECRET", raising=False)
    account = account_factory(venue=Venue.TRADING212, credential_env_prefix="T212_TEST")

    run = adapters_base.sync_account(session, account, Settings())

    assert run.status == "error"
    assert run.error == "missing credentials"
    assert run.added == 0
    assert run.skipped == 0
    tx_count = session.execute(select(func.count()).select_from(Transaction)).scalar()
    assert tx_count == 0


def test_sync_account_ok_run_then_second_sync_skips_duplicates(
    session, account_factory, monkeypatch
):
    account = account_factory(venue=Venue.TRADING212, credential_env_prefix="T212_TEST")
    stub = _StubAdapter([_deposit_draft("tx:dep-1")])
    monkeypatch.setattr(adapters_base, "build_adapter", lambda acct, settings: stub)

    run1 = adapters_base.sync_account(session, account, Settings())
    assert run1.status == "ok"
    assert run1.added == 1
    assert run1.skipped == 0
    assert stub.since_calls == [None]
    assert stub.closed is True

    run2 = adapters_base.sync_account(session, account, Settings())
    assert run2.status == "ok"
    assert run2.added == 0
    assert run2.skipped == 1
    # since = last ok run's `started` minus 7 days
    assert stub.since_calls[1] == run1.started - timedelta(days=7)

    tx_count = session.execute(select(func.count()).select_from(Transaction)).scalar()
    assert tx_count == 1


class _FakeCcxtExchange:
    """Minimal duck-typed ccxt exchange: one page of withdrawals, nothing else."""

    def __init__(self, withdrawals: list[dict]):
        self._withdrawals = withdrawals
        self._served = False

    def fetch_my_trades(self, since=None, limit=None):
        return []

    def fetch_deposits(self, since=None, limit=None):
        return []

    def fetch_withdrawals(self, since=None, limit=None):
        if self._served:
            return []
        self._served = True
        return self._withdrawals

    def fetch_ledger(self, since=None, limit=None):
        return []

    def fetch_balance(self):
        return {"total": {}}


def test_sync_account_ccxt_pending_item_warns_but_stays_ok(session, account_factory, monkeypatch):
    """A pending withdrawal (timestamp=None) must not fail the whole sync run
    — it's dropped with a warning, everything else still gets upserted."""
    fake = _FakeCcxtExchange(
        [
            {"id": "w-pending", "timestamp": None, "currency": "BTC", "amount": 1},
            {"id": "w1", "timestamp": 1_700_000_000_000, "currency": "BTC", "amount": 1},
        ]
    )
    adapter = CcxtAdapter("okx", "key", "secret", "pass", exchange=fake)
    account = account_factory(venue=Venue.OKX, credential_env_prefix="OKX_TEST")
    monkeypatch.setattr(adapters_base, "build_adapter", lambda acct, settings: adapter)

    run = adapters_base.sync_account(session, account, Settings())

    assert run.status == "ok"
    assert run.added == 1
    assert "withdrawal w-pending skipped: no timestamp (pending?)" in run.error
    tx_count = session.execute(select(func.count()).select_from(Transaction)).scalar()
    assert tx_count == 1


def test_sync_account_adapter_error_rolls_back_and_records_message(
    session, account_factory, monkeypatch
):
    account = account_factory(venue=Venue.TRADING212, credential_env_prefix="T212_TEST")

    class _FailingAdapter(_StubAdapter):
        def fetch_transactions(self, since):
            raise RuntimeError("boom")

    monkeypatch.setattr(adapters_base, "build_adapter", lambda acct, settings: _FailingAdapter([]))

    run = adapters_base.sync_account(session, account, Settings())

    assert run.status == "error"
    assert run.error == "boom"
    tx_count = session.execute(select(func.count()).select_from(Transaction)).scalar()
    assert tx_count == 0


def test_sync_endpoint_creates_and_lists_sync_runs(client, monkeypatch):
    resp = client.post(
        "/api/accounts",
        json={
            "venue": "trading212",
            "name": "t212-live",
            "kind": "broker_invest",
            "mode": "live",
            "credential_env_prefix": "T212_TEST",
        },
    )
    account = resp.json()

    stub = _StubAdapter([_deposit_draft("tx:dep-api-1")])
    monkeypatch.setattr(adapters_base, "build_adapter", lambda acct, settings: stub)

    sync_resp = client.post(f"/api/accounts/{account['id']}/sync")
    assert sync_resp.status_code == 200, sync_resp.text
    body = sync_resp.json()
    assert body["status"] == "ok"
    assert body["added"] == 1

    list_resp = client.get(f"/api/accounts/{account['id']}/sync-runs")
    assert list_resp.status_code == 200
    runs = list_resp.json()
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"


def test_build_adapter_unsupported_venue_raises_not_implemented(account_factory):
    # OKX/Kraken are wired up as of the ccxt adapter — Binance stays
    # unimplemented, so it's the stand-in for "unsupported venue" here.
    account = account_factory(venue=Venue.BINANCE, credential_env_prefix="BINANCE_TEST")
    with pytest.raises(NotImplementedError, match="binance"):
        adapters_base.build_adapter(account, Settings())


def test_sync_account_unsupported_venue_finishes_as_error_not_stuck_running(
    session, account_factory
):
    account = account_factory(venue=Venue.BINANCE, credential_env_prefix="BINANCE_TEST")

    run = adapters_base.sync_account(session, account, Settings())

    assert run.status == "error"
    assert run.error == "adapter for binance not available"
    assert run.finished is not None
    tx_count = session.execute(select(func.count()).select_from(Transaction)).scalar()
    assert tx_count == 0


def test_sync_endpoint_binance_account_returns_error_run_not_500(client):
    resp = client.post(
        "/api/accounts",
        json={
            "venue": "binance",
            "name": "binance-main",
            "kind": "crypto_spot",
            "mode": "live",
            "credential_env_prefix": "BINANCE_TEST",
        },
    )
    account = resp.json()

    sync_resp = client.post(f"/api/accounts/{account['id']}/sync")

    assert sync_resp.status_code == 200, sync_resp.text
    body = sync_resp.json()
    assert body["status"] == "error"
    assert body["error"] == "adapter for binance not available"
