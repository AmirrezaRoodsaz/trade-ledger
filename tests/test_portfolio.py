from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from trade_ledger.engine import portfolio
from trade_ledger.enums import AssetClass, Mode, TxSource, TxType, Venue
from trade_ledger.models import Price, Transaction
from trade_ledger.prices.service import PriceMissing


def _tx(session, account, type_, on, instrument=None, **kwargs):
    row = Transaction(
        account_id=account.id,
        ts=datetime(on.year, on.month, on.day, 12, tzinfo=UTC),
        type=type_,
        instrument_id=None if instrument is None else instrument.id,
        quantity=Decimal(kwargs.pop("quantity", 0)),
        amount_eur=Decimal(kwargs.pop("amount_eur", 0)),
        fee_eur=Decimal(kwargs.pop("fee_eur", 0)),
        withholding_tax_eur=Decimal(kwargs.pop("withholding_tax_eur", 0)),
        source=TxSource.MANUAL,
        **kwargs,
    )
    session.add(row)
    session.flush()
    return row


def _price(session, instrument, on, close, ccy="EUR"):
    session.add(
        Price(
            instrument_id=instrument.id,
            date=on,
            close=Decimal(close),
            ccy=ccy,
            source="manual",
        )
    )
    session.flush()


# --- holdings --------------------------------------------------------------


def test_holdings_average_cost_over_two_buys(session, account_factory, instrument_factory):
    account = account_factory()
    btc = instrument_factory(symbol="BTC")
    _tx(session, account, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=20000)
    _tx(session, account, TxType.BUY, date(2026, 2, 1), btc, quantity=1, amount_eur=30000)

    (holding,) = portfolio.holdings(session, [account.id])

    assert holding.quantity == Decimal(2)
    assert holding.avg_cost_eur == Decimal(25000)
    assert holding.cost_eur == Decimal(50000)


def test_holdings_fees_raise_the_cost_basis(session, account_factory, instrument_factory):
    account = account_factory()
    btc = instrument_factory(symbol="BTC")
    _tx(
        session, account, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=1000, fee_eur=10
    )

    (holding,) = portfolio.holdings(session, [account.id])

    assert holding.cost_eur == Decimal(1010)


def test_selling_reduces_cost_proportionally(session, account_factory, instrument_factory):
    account = account_factory()
    btc = instrument_factory(symbol="BTC")
    _tx(session, account, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=20000)
    _tx(session, account, TxType.BUY, date(2026, 2, 1), btc, quantity=1, amount_eur=30000)
    _tx(session, account, TxType.SELL, date(2026, 3, 1), btc, quantity=1, amount_eur=40000)

    (holding,) = portfolio.holdings(session, [account.id])

    assert holding.quantity == Decimal(1)
    assert holding.cost_eur == Decimal(25000)  # half of 50.000, not the 20.000 first lot
    assert holding.avg_cost_eur == Decimal(25000)


def test_split_adds_quantity_at_zero_cost(session, account_factory, instrument_factory):
    account = account_factory()
    share = instrument_factory(symbol="AAPL", asset_class=AssetClass.STOCK)
    _tx(session, account, TxType.BUY, date(2026, 1, 1), share, quantity=10, amount_eur=1000)
    _tx(session, account, TxType.SPLIT, date(2026, 2, 1), share, quantity=10)

    (holding,) = portfolio.holdings(session, [account.id])

    assert holding.quantity == Decimal(20)
    assert holding.cost_eur == Decimal(1000)
    assert holding.avg_cost_eur == Decimal(50)


def test_closed_position_drops_out(session, account_factory, instrument_factory):
    account = account_factory()
    btc = instrument_factory(symbol="BTC")
    _tx(session, account, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=20000)
    _tx(session, account, TxType.SELL, date(2026, 2, 1), btc, quantity=1, amount_eur=25000)

    assert portfolio.holdings(session, [account.id]) == []


def test_holdings_at_a_past_date_ignore_later_transactions(
    session, account_factory, instrument_factory
):
    account = account_factory()
    btc = instrument_factory(symbol="BTC")
    _tx(session, account, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=20000)
    _tx(session, account, TxType.BUY, date(2026, 2, 1), btc, quantity=1, amount_eur=30000)

    (holding,) = portfolio.holdings(session, [account.id], portfolio.end_of_day(date(2026, 1, 15)))

    assert holding.quantity == Decimal(1)


def test_missing_price_leaves_value_none_not_zero(session, account_factory, instrument_factory):
    account = account_factory()
    btc = instrument_factory(symbol="BTC")
    _tx(session, account, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=20000)

    (holding,) = portfolio.holdings(session, [account.id])

    assert holding.price_eur is None
    assert holding.value_eur is None
    assert holding.unrealised_eur is None
    assert holding.weight == Decimal(0)


def test_price_uses_the_latest_cached_date_at_or_before_at(
    session, account_factory, instrument_factory
):
    account = account_factory()
    btc = instrument_factory(symbol="BTC")
    _tx(session, account, TxType.BUY, date(2026, 1, 1), btc, quantity=2, amount_eur=20000)
    _price(session, btc, date(2026, 1, 2), 11000)
    _price(session, btc, date(2026, 1, 9), 12000)  # after `at`, must be ignored

    (holding,) = portfolio.holdings(session, [account.id], portfolio.end_of_day(date(2026, 1, 5)))

    assert holding.price_eur == Decimal(11000)
    assert holding.value_eur == Decimal(22000)
    assert holding.unrealised_eur == Decimal(2000)


def test_holding_weights_sum_to_one(session, account_factory, instrument_factory):
    account = account_factory()
    btc = instrument_factory(symbol="BTC")
    eth = instrument_factory(symbol="ETH")
    _tx(session, account, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=1000)
    _tx(session, account, TxType.BUY, date(2026, 1, 1), eth, quantity=10, amount_eur=1000)
    _price(session, btc, date(2026, 1, 2), 3000)
    _price(session, eth, date(2026, 1, 2), 100)

    rows = portfolio.holdings(session, [account.id], portfolio.end_of_day(date(2026, 1, 2)))

    assert sum(h.weight for h in rows) == Decimal(1)
    assert {h.instrument.symbol: h.weight for h in rows} == {
        "BTC": Decimal("0.75"),
        "ETH": Decimal("0.25"),
    }


# --- cash flows and valuation ---------------------------------------------


def test_cashflows_are_signed_and_bounded_by_the_period(
    session, account_factory, instrument_factory
):
    account = account_factory(name="okx-paper")
    _tx(session, account, TxType.DEPOSIT, date(2026, 1, 1), amount_eur=1000)
    _tx(session, account, TxType.FEE, date(2026, 1, 2), amount_eur=5)
    _tx(session, account, TxType.WITHDRAWAL, date(2026, 1, 3), amount_eur=200)
    _tx(session, account, TxType.DEPOSIT, date(2026, 2, 1), amount_eur=999)  # outside

    rows = portfolio.cashflows(session, [account.id], date(2026, 1, 1), date(2026, 1, 31))

    assert [(r["date"].day, r["type"], r["amount_eur"]) for r in rows] == [
        (1, TxType.DEPOSIT, Decimal(1000)),
        (2, TxType.FEE, Decimal(-5)),
        (3, TxType.WITHDRAWAL, Decimal(-200)),
    ]
    assert {r["account"] for r in rows} == {"okx-paper"}


def test_external_flows_are_deposits_and_withdrawals_only(session, account_factory):
    account = account_factory()
    _tx(session, account, TxType.DEPOSIT, date(2026, 1, 1), amount_eur=1000)
    _tx(session, account, TxType.WITHDRAWAL, date(2026, 1, 3), amount_eur=200)
    _tx(session, account, TxType.DIVIDEND, date(2026, 1, 4), amount_eur=50)

    flows = portfolio.external_flows(session, [account.id], date(2026, 1, 1), date(2026, 1, 31))

    assert flows == [(date(2026, 1, 1), Decimal(1000)), (date(2026, 1, 3), Decimal(-200))]


def test_portfolio_value_is_cash_plus_holdings(session, account_factory, instrument_factory):
    account = account_factory()
    btc = instrument_factory(symbol="BTC")
    _tx(session, account, TxType.DEPOSIT, date(2026, 1, 1), amount_eur=5000)
    _tx(session, account, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=4000)
    _price(session, btc, date(2026, 1, 2), 4500)

    assert portfolio.portfolio_value(session, [account.id], date(2026, 1, 2)) == Decimal(5500)


def test_portfolio_value_raises_when_a_price_is_missing(
    session, account_factory, instrument_factory
):
    account = account_factory()
    btc = instrument_factory(symbol="BTC")
    _tx(session, account, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=4000)

    with pytest.raises(PriceMissing):
        portfolio.portfolio_value(session, [account.id], date(2026, 1, 2))


def test_value_series_skips_dates_without_a_price(session, account_factory, instrument_factory):
    account = account_factory()
    btc = instrument_factory(symbol="BTC")
    _tx(session, account, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=1000)
    _price(session, btc, date(2026, 1, 2), 1100)

    series = portfolio.value_series(session, [account.id], date(2026, 1, 1), date(2026, 1, 3))

    assert series == [
        (date(2026, 1, 2), Decimal(100)),  # 1.100 held minus 1.000 of negative cash
        (date(2026, 1, 3), Decimal(100)),
    ]


# --- allocation, dividends, fees ------------------------------------------


def test_allocation_by_asset_class_weights_sum_to_one(session, account_factory, instrument_factory):
    account = account_factory()
    btc = instrument_factory(symbol="BTC", asset_class=AssetClass.CRYPTO)
    share = instrument_factory(symbol="AAPL", asset_class=AssetClass.STOCK)
    _tx(session, account, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=1000)
    _tx(session, account, TxType.BUY, date(2026, 1, 1), share, quantity=10, amount_eur=1000)
    _price(session, btc, date(2026, 1, 1), 3000)
    _price(session, share, date(2026, 1, 1), 100)

    rows = portfolio.allocation(session, [account.id], "asset_class")

    assert [r["key"] for r in rows] == [AssetClass.CRYPTO, AssetClass.STOCK]
    assert [r["value_eur"] for r in rows] == [Decimal(3000), Decimal(1000)]
    assert sum(r["weight"] for r in rows) == Decimal(1)


def test_allocation_by_venue_splits_the_same_instrument(
    session, account_factory, instrument_factory
):
    okx = account_factory(venue=Venue.OKX)
    kraken = account_factory(venue=Venue.KRAKEN)
    btc = instrument_factory(symbol="BTC")
    _tx(session, okx, TxType.BUY, date(2026, 1, 1), btc, quantity=3, amount_eur=3000)
    _tx(session, kraken, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=1000)
    _price(session, btc, date(2026, 1, 1), 1000)

    rows = portfolio.allocation(session, [okx.id, kraken.id], "venue")

    assert [(r["key"], r["weight"]) for r in rows] == [
        (Venue.OKX, Decimal("0.75")),
        (Venue.KRAKEN, Decimal("0.25")),
    ]


def test_allocation_rejects_an_unknown_grouping(session, account_factory):
    with pytest.raises(ValueError, match="asset_class"):
        portfolio.allocation(session, [account_factory().id], "colour")


def test_dividends_by_month_and_run_rate_of_still_held_positions(
    session, account_factory, instrument_factory
):
    account = account_factory()
    kept = instrument_factory(symbol="AAPL", asset_class=AssetClass.STOCK)
    sold = instrument_factory(symbol="MSFT", asset_class=AssetClass.STOCK)
    _tx(session, account, TxType.BUY, date(2026, 1, 1), kept, quantity=10, amount_eur=1000)
    _tx(session, account, TxType.BUY, date(2026, 1, 1), sold, quantity=10, amount_eur=1000)
    _tx(session, account, TxType.SELL, date(2026, 6, 1), sold, quantity=10, amount_eur=1200)
    _tx(
        session,
        account,
        TxType.DIVIDEND,
        date(2026, 2, 15),
        kept,
        amount_eur=30,
        withholding_tax_eur=5,
    )
    _tx(session, account, TxType.DIVIDEND, date(2026, 8, 15), kept, amount_eur=40)
    _tx(session, account, TxType.DIVIDEND, date(2026, 3, 15), sold, amount_eur=25)
    _tx(session, account, TxType.DIVIDEND, date(2025, 8, 15), kept, amount_eur=99)  # other year

    result = portfolio.dividends(session, [account.id], 2026)

    assert result["total"] == Decimal(95)
    assert result["withholding"] == Decimal(5)
    assert result["by_instrument"] == {"AAPL": Decimal(70), "MSFT": Decimal(25)}
    assert result["by_month"] == {
        "2026-02": Decimal(30),
        "2026-03": Decimal(25),
        "2026-08": Decimal(40),
    }
    assert result["projected_next_12m"] == Decimal(70)  # MSFT is gone


def test_fees_add_carried_and_standalone_fees_per_account(
    session, account_factory, instrument_factory
):
    okx = account_factory(name="okx-paper")
    t212 = account_factory(name="t212-paper")
    btc = instrument_factory(symbol="BTC")
    _tx(session, okx, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=1000, fee_eur=2)
    _tx(session, okx, TxType.FEE, date(2026, 2, 1), amount_eur=3)
    _tx(session, t212, TxType.FEE, date(2026, 2, 1), amount_eur=7)
    _tx(session, t212, TxType.FEE, date(2025, 2, 1), amount_eur=99)  # other year

    result = portfolio.fees(session, [okx.id, t212.id], 2026)

    assert result["by_account"] == {"okx-paper": Decimal(5), "t212-paper": Decimal(7)}
    assert result["total"] == Decimal(12)


# --- API ------------------------------------------------------------------


def test_holdings_route_defaults_to_paper_mode(
    client, session, account_factory, instrument_factory
):
    paper = account_factory(mode=Mode.PAPER)
    live = account_factory(mode=Mode.LIVE)
    btc = instrument_factory(symbol="BTC")
    eth = instrument_factory(symbol="ETH")
    _tx(session, paper, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=1000)
    _tx(session, live, TxType.BUY, date(2026, 1, 1), eth, quantity=1, amount_eur=1000)
    _price(session, btc, date(2026, 1, 1), 1200)

    resp = client.get("/api/portfolio/holdings", params={"at": "2026-01-01"})

    assert resp.status_code == 200, resp.text
    (holding,) = resp.json()
    assert holding["instrument"]["symbol"] == "BTC"
    assert holding["quantity"] == "1"
    assert holding["value_eur"] == "1200"
    assert holding["unrealised_eur"] == "200"
    assert holding["weight"] == "1"

    both = client.get("/api/portfolio/holdings", params={"at": "2026-01-01", "mode": "all"})
    assert {h["instrument"]["symbol"] for h in both.json()} == {"BTC", "ETH"}

    only_live = client.get(
        "/api/portfolio/holdings", params={"at": "2026-01-01", "account_id": live.id}
    )
    assert [h["instrument"]["symbol"] for h in only_live.json()] == ["ETH"]


def test_holdings_route_404s_on_an_unknown_account(client):
    assert client.get("/api/portfolio/holdings", params={"account_id": 999}).status_code == 404


def test_cashflows_and_fees_routes(client, session, account_factory):
    account = account_factory(name="okx-paper")
    _tx(session, account, TxType.DEPOSIT, date(2026, 1, 1), amount_eur=1000)
    _tx(session, account, TxType.FEE, date(2026, 1, 2), amount_eur=5)

    rows = client.get(
        "/api/portfolio/cashflows", params={"from": "2026-01-01", "to": "2026-01-31"}
    ).json()
    assert [r["amount_eur"] for r in rows] == ["1000", "-5"]

    fees = client.get("/api/portfolio/fees", params={"year": 2026}).json()
    assert fees == {"by_account": {"okx-paper": "5"}, "total": "5"}


def test_value_series_route_reports_missing_dates(
    client, session, account_factory, instrument_factory
):
    account = account_factory()
    btc = instrument_factory(symbol="BTC")
    _tx(session, account, TxType.DEPOSIT, date(2026, 1, 1), amount_eur=1000)
    _tx(session, account, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=1000)
    _price(session, btc, date(2026, 1, 2), 1100)

    body = client.get(
        "/api/portfolio/value-series", params={"from": "2026-01-01", "to": "2026-01-03"}
    ).json()

    assert body["missing_dates"] == ["2026-01-01"]
    assert body["points"] == [
        {"date": "2026-01-02", "value_eur": "1100"},
        {"date": "2026-01-03", "value_eur": "1100"},
    ]


def test_allocation_and_dividends_routes(client, session, account_factory, instrument_factory):
    account = account_factory()
    share = instrument_factory(symbol="AAPL", asset_class=AssetClass.STOCK)
    _tx(session, account, TxType.BUY, date(2026, 1, 1), share, quantity=10, amount_eur=1000)
    _tx(session, account, TxType.DIVIDEND, date(2026, 2, 1), share, amount_eur=30)
    _price(session, share, date(2026, 1, 1), 120)

    allocation = client.get("/api/portfolio/allocation", params={"by": "instrument"}).json()
    assert allocation == [{"key": "AAPL", "value_eur": "1200", "weight": "1"}]

    dividends = client.get("/api/portfolio/dividends", params={"year": 2026}).json()
    assert dividends["total"] == "30"
    assert dividends["by_month"] == {"2026-02": "30"}
    assert dividends["projected_next_12m"] == "30"


def test_returns_route(client, session, account_factory, instrument_factory):
    account = account_factory()
    btc = instrument_factory(symbol="BTC")
    _tx(session, account, TxType.DEPOSIT, date(2025, 1, 1), amount_eur=1000)
    _tx(session, account, TxType.BUY, date(2025, 1, 1), btc, quantity=1, amount_eur=1000)
    _price(session, btc, date(2025, 1, 1), 1000)
    _tx(session, account, TxType.DEPOSIT, date(2025, 7, 2), amount_eur=900)
    _price(session, btc, date(2025, 7, 2), 1100)
    _price(session, btc, date(2026, 1, 1), 1210)

    body = client.get(
        "/api/portfolio/returns", params={"from": "2025-01-01", "to": "2026-01-01"}
    ).json()

    # 1.000 -> 1.100 before the deposit, then 2.000 -> 2.110: 1,1 * 1,055 - 1.
    assert body["ttwror"] == "0.1605"
    assert body["start_value"] == "1000"
    assert body["end_value"] == "2110"
    assert body["net_flows"] == "900"
    assert round(Decimal(body["xirr"]), 3) == Decimal("0.146")


def test_returns_route_is_null_when_a_price_is_missing(
    client, session, account_factory, instrument_factory
):
    account = account_factory()
    btc = instrument_factory(symbol="BTC")
    _tx(session, account, TxType.BUY, date(2026, 1, 1), btc, quantity=1, amount_eur=1000)

    body = client.get(
        "/api/portfolio/returns", params={"from": "2026-01-01", "to": "2026-01-31"}
    ).json()

    assert body == {
        "ttwror": None,
        "xirr": None,
        "start_value": None,
        "end_value": None,
        "net_flows": "0",
    }
