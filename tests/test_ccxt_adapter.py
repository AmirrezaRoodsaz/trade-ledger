from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_ledger.adapters import base as adapters_base
from trade_ledger.adapters import ccxt_adapter as ccxt_adapter_module
from trade_ledger.adapters.ccxt_adapter import CcxtAdapter
from trade_ledger.enums import Mode, TxType, Venue
from trade_ledger.settings import Settings


class _FakeExchange:
    """Duck-typed stand-in for a ccxt exchange: only the five read methods
    the adapter is allowed to call, each paging through a list of pages."""

    def __init__(self, trades=None, deposits=None, withdrawals=None, ledger=None, balance=None):
        self._pages = {
            "trades": list(trades or []),
            "deposits": list(deposits or []),
            "withdrawals": list(withdrawals or []),
            "ledger": list(ledger or []),
        }
        self._calls = {k: 0 for k in self._pages}
        self.balance = balance or {"total": {}}
        self.sandbox_calls: list[bool] = []

    def _next_page(self, key: str) -> list[dict]:
        pages = self._pages[key]
        i = self._calls[key]
        self._calls[key] += 1
        return pages[i] if i < len(pages) else []

    def fetch_my_trades(self, since=None, limit=None):
        return self._next_page("trades")

    def fetch_deposits(self, since=None, limit=None):
        return self._next_page("deposits")

    def fetch_withdrawals(self, since=None, limit=None):
        return self._next_page("withdrawals")

    def fetch_ledger(self, since=None, limit=None):
        return self._next_page("ledger")

    def fetch_balance(self):
        return self.balance

    def set_sandbox_mode(self, enabled):
        self.sandbox_calls.append(enabled)


def _adapter(exchange_id="okx", **fixtures) -> CcxtAdapter:
    fake = _FakeExchange(**fixtures)
    return CcxtAdapter(exchange_id, "key", "secret", exchange=fake)


def _trade(id_, ts_ms, symbol, side, amount, price, cost, fee_cost=None, fee_ccy=None):
    trade = {
        "id": id_,
        "timestamp": ts_ms,
        "symbol": symbol,
        "side": side,
        "amount": amount,
        "price": price,
        "cost": cost,
    }
    if fee_cost is not None:
        trade["fee"] = {"cost": fee_cost, "currency": fee_ccy}
    return trade


# -- mapping ------------------------------------------------------------------


def test_trade_mapping_eur_quote():
    adapter = _adapter(
        trades=[[_trade("1", 1_700_000_000_000, "BTC/EUR", "buy", 0.5, 20000, 10000, 5, "EUR")]]
    )
    drafts = adapter.fetch_transactions(since=None)
    trade = next(d for d in drafts if d.external_id == "trade:1")

    assert trade.type == TxType.BUY
    assert trade.quantity == Decimal("0.5")
    assert trade.instrument_symbol == "BTC"
    assert trade.price_ccy == "EUR"
    assert trade.amount_eur == Decimal(10000)
    assert trade.fee_eur == Decimal(5)
    assert trade.fx_source is None


def test_trade_mapping_usdt_quote_pending_fx():
    adapter = _adapter(
        trades=[
            [_trade("2", 1_700_000_000_000, "BTC/USDT", "sell", 0.25, 60000, 15000, 15, "USDT")]
        ]
    )
    drafts = adapter.fetch_transactions(since=None)
    trade = next(d for d in drafts if d.external_id == "trade:2")

    assert trade.type == TxType.SELL
    assert trade.price_ccy == "USDT"
    assert trade.amount_eur == Decimal(0)
    assert trade.fee_eur == Decimal(0)
    assert trade.fee == Decimal(15)
    assert trade.fee_ccy == "USDT"
    assert trade.fx_source == "pending"


def test_deposit_and_withdrawal_mapping():
    deposit = {"id": "d1", "timestamp": 1_700_000_000_000, "currency": "EUR", "amount": 500}
    withdrawal = {"id": "w1", "timestamp": 1_700_000_000_000, "currency": "ETH", "amount": 2}
    adapter = _adapter(deposits=[[deposit]], withdrawals=[[withdrawal]])
    drafts = adapter.fetch_transactions(since=None)

    dep = next(d for d in drafts if d.external_id == "dep:d1")
    assert dep.type == TxType.DEPOSIT
    assert dep.amount_eur == Decimal(500)
    assert dep.instrument_symbol is None

    wd = next(d for d in drafts if d.external_id == "wd:w1")
    assert wd.type == TxType.TRANSFER_OUT
    assert wd.instrument_symbol == "ETH"
    assert wd.quantity == Decimal(2)
    assert wd.amount_eur == Decimal(0)


def test_staking_ledger_entry_mapped_pending_fx():
    entry = {
        "id": "l1",
        "timestamp": 1_700_000_000_000,
        "type": "staking",
        "currency": "SOL",
        "amount": 1.2,
    }
    non_staking = {
        "id": "l2",
        "timestamp": 1_700_000_000_000,
        "type": "trade",
        "currency": "BTC",
        "amount": 1,
    }
    adapter = _adapter(ledger=[[entry, non_staking]])
    drafts = adapter.fetch_transactions(since=None)

    assert len(drafts) == 1
    reward = drafts[0]
    assert reward.external_id == "ledger:l1"
    assert reward.type == TxType.STAKING_REWARD
    assert reward.instrument_symbol == "SOL"
    assert reward.quantity == Decimal("1.2")
    assert reward.fx_source == "pending"


def test_pending_item_without_timestamp_skipped_with_warning():
    # Pending deposits/withdrawals on OKX/Kraken routinely carry timestamp=None.
    pending = {"id": "w-pending", "timestamp": None, "currency": "BTC", "amount": 1}
    dated = {"id": "w1", "timestamp": 1_700_000_000_000, "currency": "BTC", "amount": 1}
    adapter = _adapter(withdrawals=[[pending, dated]])

    drafts = adapter.fetch_transactions(since=None)

    assert [d.external_id for d in drafts] == ["wd:w1"]
    assert "withdrawal w-pending skipped: no timestamp (pending?)" in adapter.warnings


def test_fetch_balances_maps_totals_to_decimal():
    adapter = _adapter(balance={"total": {"BTC": 0.5, "EUR": 1000}})

    balances = adapter.fetch_balances()

    assert balances == {"BTC": Decimal("0.5"), "EUR": Decimal(1000)}


# -- pagination -----------------------------------------------------------------


def test_pagination_stops_when_page_smaller_than_limit(monkeypatch):
    monkeypatch.setattr(ccxt_adapter_module, "_PAGE_LIMIT", 2)
    page1 = [
        _trade("1", 1000, "BTC/EUR", "buy", 1, 100, 100),
        _trade("2", 2000, "BTC/EUR", "buy", 1, 100, 100),
    ]
    page2 = [_trade("3", 3000, "BTC/EUR", "buy", 1, 100, 100)]
    fake = _FakeExchange(trades=[page1, page2])
    adapter = CcxtAdapter("okx", "key", "secret", exchange=fake)

    drafts = adapter.fetch_transactions(since=None)

    trade_ids = {d.external_id for d in drafts}
    assert trade_ids == {"trade:1", "trade:2", "trade:3"}
    assert fake._calls["trades"] == 2  # stopped once a page came back under the limit


def test_pagination_hard_cap_stops_even_when_pages_never_shrink(monkeypatch):
    monkeypatch.setattr(ccxt_adapter_module, "_PAGE_LIMIT", 1)
    monkeypatch.setattr(ccxt_adapter_module, "_MAX_PAGES", 3)
    # Every page is full-sized (== limit) and timestamps keep advancing, so
    # only the hard cap on page count stops the loop.
    pages = [[_trade(str(i), i * 1000, "BTC/EUR", "buy", 1, 100, 100)] for i in range(1, 10)]
    fake = _FakeExchange(trades=pages)
    adapter = CcxtAdapter("okx", "key", "secret", exchange=fake)

    drafts = adapter.fetch_transactions(since=None)

    assert fake._calls["trades"] == 3
    assert len([d for d in drafts if d.type == TxType.BUY]) == 3


# -- demo / sandbox ---------------------------------------------------------------


def test_demo_flag_sets_sandbox_mode():
    fake = _FakeExchange()
    adapter = CcxtAdapter("okx", "key", "secret", "pass", demo=True, exchange=fake)
    assert fake.sandbox_calls == [True]
    assert adapter.warnings == []


def test_kraken_demo_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="kraken has no demo mode"):
        CcxtAdapter("kraken", "key", "secret", demo=True, exchange=_FakeExchange())


# -- OKX 3-month window warning ------------------------------------------------


def test_okx_window_warning_when_since_none():
    adapter = _adapter("okx")
    adapter.fetch_transactions(since=None)
    assert adapter.warnings == [
        "OKX API only returns 3 months; import the quarterly archive CSV for older data"
    ]


def test_okx_window_warning_when_since_older_than_85_days():
    adapter = _adapter("okx")
    old_since = datetime.now(UTC) - timedelta(days=100)
    adapter.fetch_transactions(since=old_since)
    assert len(adapter.warnings) == 1


def test_okx_no_warning_when_since_recent():
    adapter = _adapter("okx")
    recent_since = datetime.now(UTC) - timedelta(days=10)
    adapter.fetch_transactions(since=recent_since)
    assert adapter.warnings == []


def test_kraken_never_warns():
    adapter = _adapter("kraken")
    adapter.fetch_transactions(since=None)
    assert adapter.warnings == []


# -- build_adapter dispatch --------------------------------------------------------


def test_build_adapter_dispatches_okx(account_factory, monkeypatch):
    monkeypatch.setenv("OKX_TEST_API_KEY", "k")
    monkeypatch.setenv("OKX_TEST_API_SECRET", "s")
    monkeypatch.setenv("OKX_TEST_API_PASSPHRASE", "p")
    account = account_factory(venue=Venue.OKX, credential_env_prefix="OKX_TEST", mode=Mode.DEMO)

    adapter = adapters_base.build_adapter(account, Settings())

    assert isinstance(adapter, CcxtAdapter)
    assert adapter._exchange_id == "okx"


def test_build_adapter_okx_missing_passphrase_raises(account_factory, monkeypatch):
    monkeypatch.setenv("OKX_TEST_API_KEY", "k")
    monkeypatch.setenv("OKX_TEST_API_SECRET", "s")
    monkeypatch.delenv("OKX_TEST_API_PASSPHRASE", raising=False)
    account = account_factory(venue=Venue.OKX, credential_env_prefix="OKX_TEST")

    with pytest.raises(adapters_base.MissingCredentialsError):
        adapters_base.build_adapter(account, Settings())


def test_build_adapter_dispatches_kraken(account_factory, monkeypatch):
    monkeypatch.setenv("KRAKEN_TEST_API_KEY", "k")
    monkeypatch.setenv("KRAKEN_TEST_API_SECRET", "s")
    account = account_factory(venue=Venue.KRAKEN, credential_env_prefix="KRAKEN_TEST")

    adapter = adapters_base.build_adapter(account, Settings())

    assert isinstance(adapter, CcxtAdapter)
    assert adapter._exchange_id == "kraken"


def test_build_adapter_kraken_missing_credentials_raises(account_factory, monkeypatch):
    monkeypatch.delenv("KRAKEN_TEST_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_TEST_API_SECRET", raising=False)
    account = account_factory(venue=Venue.KRAKEN, credential_env_prefix="KRAKEN_TEST")

    with pytest.raises(adapters_base.MissingCredentialsError):
        adapters_base.build_adapter(account, Settings())
