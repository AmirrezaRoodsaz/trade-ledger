from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path

import httpx

from trade_ledger.adapters.trading212 import Trading212Adapter, _price_symbol
from trade_ledger.enums import AssetClass, TxType

_FIXTURES = Path(__file__).parent / "fixtures" / "t212"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def _adapter(handler) -> Trading212Adapter:
    client = httpx.Client(
        base_url="https://demo.trading212.com", transport=httpx.MockTransport(handler)
    )
    return Trading212Adapter("key", "secret", demo=True, client=client)


def _instruments_or(handler_for_others):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v0/equity/metadata/instruments":
            return httpx.Response(200, json=_load("instruments.json"))
        return handler_for_others(request)

    return handler


def test_order_pagination_follows_two_pages():
    calls: list[str] = []

    def others(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "cursor=222" in str(request.url):
            return httpx.Response(200, json=_load("orders_page2.json"))
        if request.url.path.endswith("/history/orders"):
            return httpx.Response(200, json=_load("orders_page1.json"))
        return httpx.Response(200, json=_load("empty.json"))

    adapter = _adapter(_instruments_or(others))
    drafts = adapter.fetch_transactions(since=None)
    order_drafts = [d for d in drafts if d.type in (TxType.BUY, TxType.SELL)]

    assert len(order_drafts) == 2
    assert any("cursor=222" in c for c in calls)


def test_buy_mapping_non_eur_leaves_amount_pending():
    def others(request: httpx.Request) -> httpx.Response:
        # orders_page1.json advertises a nextPagePath (cursor=222) — a real
        # server would eventually run out of pages, so the mock must too, or
        # the adapter's (correct) "follow nextPagePath" loop never ends.
        if "cursor=222" in str(request.url):
            return httpx.Response(200, json=_load("empty.json"))
        if request.url.path.endswith("/history/orders"):
            return httpx.Response(200, json=_load("orders_page1.json"))
        return httpx.Response(200, json=_load("empty.json"))

    adapter = _adapter(_instruments_or(others))
    drafts = adapter.fetch_transactions(since=None)
    buy = next(d for d in drafts if d.external_id == "order:5001")

    assert buy.type == TxType.BUY
    assert buy.quantity == Decimal(2)
    assert buy.price == Decimal("150.25")
    assert buy.price_ccy == "USD"
    assert buy.amount_eur == Decimal(0)
    assert buy.fx_source == "pending"
    assert buy.isin == "US0378331005"
    assert buy.asset_class == AssetClass.STOCK


def test_buy_mapping_eur_uses_filled_value():
    def others(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/history/orders"):
            return httpx.Response(200, json=_load("orders_page2.json"))
        return httpx.Response(200, json=_load("empty.json"))

    adapter = _adapter(_instruments_or(others))
    drafts = adapter.fetch_transactions(since=None)
    buy = next(d for d in drafts if d.external_id == "order:5002")

    assert buy.amount_eur == Decimal("316.5")
    assert buy.fx_source is None
    assert buy.asset_class == AssetClass.ETF


def test_dividend_mapping():
    def others(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/history/dividends"):
            return httpx.Response(200, json=_load("dividends.json"))
        return httpx.Response(200, json=_load("empty.json"))

    adapter = _adapter(_instruments_or(others))
    drafts = adapter.fetch_transactions(since=None)
    dividend = next(d for d in drafts if d.type == TxType.DIVIDEND)

    assert dividend.amount_eur == Decimal("1.1")
    assert dividend.external_id == "div:div-ref-001"
    assert dividend.instrument_symbol == "AAPL_US_EQ"
    assert dividend.withholding_tax_eur == Decimal(0)


def test_cash_transaction_mapping_skips_unmapped_types():
    def others(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/history/transactions"):
            return httpx.Response(200, json=_load("transactions.json"))
        return httpx.Response(200, json=_load("empty.json"))

    adapter = _adapter(_instruments_or(others))
    drafts = adapter.fetch_transactions(since=None)
    cash_drafts = {d.external_id: d for d in drafts if d.external_id and d.external_id.startswith("tx:")}

    assert "tx:tx-dep-001" in cash_drafts
    assert cash_drafts["tx:tx-dep-001"].type == TxType.DEPOSIT
    assert cash_drafts["tx:tx-dep-001"].amount_eur == Decimal(500)
    assert "tx:tx-fee-001" in cash_drafts
    assert cash_drafts["tx:tx-fee-001"].type == TxType.FEE
    # TRANSFER has no TxType home yet — must be skipped, not guessed at
    assert "tx:tx-transfer-001" not in cash_drafts


def test_rate_limit_retries_after_429_then_succeeds():
    attempts = {"n": 0}

    def others(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/history/orders"):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(
                    429, headers={"x-ratelimit-reset": str(int(time.time()))}, json={}
                )
            return httpx.Response(200, json=_load("orders_page2.json"))
        return httpx.Response(200, json=_load("empty.json"))

    adapter = _adapter(_instruments_or(others))
    drafts = adapter.fetch_transactions(since=None)

    assert attempts["n"] == 2
    assert any(d.type == TxType.BUY for d in drafts)


def test_price_symbol_heuristic():
    assert _price_symbol("AAPL_US_EQ") == "aapl.us"
    assert _price_symbol("VODl_EQ") == "vod.uk"
    assert _price_symbol("SAPd_EQ") == "sap.de"
    assert _price_symbol("UNKNOWN_TICKER") is None
