"""One fixture year through `summarize`, plus the tax routes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from trade_ledger.enums import (
    AccountKind,
    AssetClass,
    FundType,
    Mode,
    TxSource,
    TxType,
    Venue,
)
from trade_ledger.models import Price, Transaction
from trade_ledger.tax.cli import print_year
from trade_ledger.tax.year_summary import summarize

D = Decimal


def ts(year, month, day) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=UTC)


@pytest.fixture()
def tx_factory(session):
    def make(account, instrument, type_, when, **kwargs) -> Transaction:
        row = Transaction(
            account_id=account.id,
            instrument_id=None if instrument is None else instrument.id,
            type=type_,
            ts=when,
            source=TxSource.MANUAL,
            quantity=kwargs.pop("quantity", D(0)),
            amount_eur=kwargs.pop("amount_eur", D(0)),
            fee_eur=kwargs.pop("fee_eur", D(0)),
            **kwargs,
        )
        session.add(row)
        session.flush()
        return row

    return make


@pytest.fixture()
def price_factory(session):
    def make(instrument, on: date, close: Decimal, ccy: str = "EUR") -> Price:
        row = Price(instrument_id=instrument.id, date=on, close=close, ccy=ccy, source="manual")
        session.add(row)
        session.flush()
        return row

    return make


@pytest.fixture()
def okx(account_factory):
    return account_factory(name="okx-live", kind=AccountKind.CRYPTO_SPOT, mode=Mode.LIVE)


@pytest.fixture()
def broker(account_factory):
    return account_factory(
        name="t212-live",
        venue=Venue.TRADING212,
        kind=AccountKind.BROKER_INVEST,
        mode=Mode.LIVE,
    )


@pytest.fixture()
def btc(instrument_factory):
    return instrument_factory(symbol="BTC", asset_class=AssetClass.CRYPTO)


@pytest.fixture()
def etf(instrument_factory):
    return instrument_factory(symbol="VWCE", asset_class=AssetClass.ETF, fund_type=FundType.AKTIEN)


# --- Section 23 Freigrenze ---------------------------------------------------


def _crypto_year(okx, btc, tx_factory, proceeds):
    tx_factory(okx, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(10000))
    tx_factory(okx, btc, TxType.SELL, ts(2025, 6, 1), quantity=D(1), amount_eur=proceeds)


def test_gain_below_the_freigrenze_is_not_exceeded(session, okx, btc, tx_factory):
    _crypto_year(okx, btc, tx_factory, D(10800))

    summary = summarize(session, 2025)

    assert summary.p23.taxable_gains == D(800)
    assert summary.p23.taxable_losses == D(0)
    assert summary.p23.net == D(800)
    assert summary.p23.freigrenze == D(1000)
    assert summary.p23.exceeded is False


def test_gain_at_the_freigrenze_is_fully_taxable(session, okx, btc, tx_factory):
    _crypto_year(okx, btc, tx_factory, D(11200))

    summary = summarize(session, 2025)

    assert summary.p23.net == D(1200)
    assert summary.p23.exceeded is True
    assert summary.p23.proceeds == D(11200)
    assert summary.p23.cost == D(10000)


def test_fees_are_werbungskosten_and_leave_the_net_consistent(session, okx, btc, tx_factory):
    tx_factory(
        okx, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(10000), fee_eur=D(10)
    )
    tx_factory(
        okx, btc, TxType.SELL, ts(2025, 6, 1), quantity=D(1), amount_eur=D(11200), fee_eur=D(20)
    )

    p23 = summarize(session, 2025).p23

    assert p23.cost == D(10010)  # acquisition fee raises the basis
    assert p23.werbungskosten == D(20)
    assert p23.net == p23.proceeds - p23.cost - p23.werbungskosten == D(1170)


def test_a_disposal_after_one_year_is_tax_free(session, okx, btc, tx_factory):
    tx_factory(okx, btc, TxType.BUY, ts(2024, 1, 10), quantity=D(1), amount_eur=D(10000))
    tx_factory(okx, btc, TxType.SELL, ts(2025, 6, 1), quantity=D(1), amount_eur=D(11200))

    p23 = summarize(session, 2025).p23

    assert p23.taxable_gains == D(0)
    assert p23.taxfree_gains == D(1200)
    assert p23.net == D(0)
    assert p23.exceeded is False


def test_staking_rewards_are_section_22_income(session, okx, btc, tx_factory):
    tx_factory(
        okx, btc, TxType.STAKING_REWARD, ts(2025, 3, 1), quantity=D("0.01"), amount_eur=D(300)
    )

    p22 = summarize(session, 2025).p22

    assert p22.income == D(300)
    assert p22.net == D(300)
    assert p22.freigrenze == D(256)
    assert p22.exceeded is True


# --- Section 20 --------------------------------------------------------------


def test_dividend_with_withholding_lands_in_p20(session, broker, instrument_factory, tx_factory):
    apple = instrument_factory(symbol="AAPL", asset_class=AssetClass.STOCK)
    tx_factory(
        broker,
        apple,
        TxType.DIVIDEND,
        ts(2025, 5, 2),
        amount_eur=D(100),
        withholding_tax_eur=D(15),
    )

    p20 = summarize(session, 2025).p20

    assert p20.dividends == D(100)
    assert p20.withholding_tax == D(15)
    assert p20.sonstige_net == D(100)
    assert p20.total_foreign == D(100)
    assert p20.sparerpauschbetrag == D(1000)


def test_share_sale_splits_into_aktien_gains(session, broker, instrument_factory, tx_factory):
    apple = instrument_factory(symbol="AAPL", asset_class=AssetClass.STOCK)
    tx_factory(broker, apple, TxType.BUY, ts(2025, 1, 2), quantity=D(10), amount_eur=D(1000))
    tx_factory(broker, apple, TxType.SELL, ts(2025, 9, 2), quantity=D(10), amount_eur=D(1300))

    summary = summarize(session, 2025)

    assert summary.p20.aktien_gains == D(300)
    assert summary.p20.aktien_losses == D(0)
    assert summary.p23.net == D(0)
    assert summary.inv == []


def test_etf_sale_lands_in_inv_not_in_aktien(session, broker, etf, tx_factory):
    tx_factory(broker, etf, TxType.BUY, ts(2024, 1, 2), quantity=D(10), amount_eur=D(1000))
    tx_factory(broker, etf, TxType.SELL, ts(2025, 9, 2), quantity=D(10), amount_eur=D(1200))

    summary = summarize(session, 2025)

    assert summary.p20.aktien_gains == D(0)
    (row,) = summary.inv
    assert row.instrument.symbol == "VWCE"
    assert row.fund_type == FundType.AKTIEN
    assert row.teilfreistellung_pct == 30
    assert row.sale_gain == D(200)
    assert row.sale_loss == D(0)
    assert summary.p20.total_foreign == D(200)


def test_etf_distribution_is_a_kap_inv_row_not_a_dividend(session, broker, etf, tx_factory):
    tx_factory(broker, etf, TxType.BUY, ts(2025, 1, 2), quantity=D(10), amount_eur=D(1000))
    tx_factory(
        broker, etf, TxType.DIVIDEND, ts(2025, 7, 1), amount_eur=D(12), withholding_tax_eur=D(2)
    )

    summary = summarize(session, 2025)

    assert summary.p20.dividends == D(0)
    assert summary.p20.withholding_tax == D(2)  # still creditable, KAP line 41
    (row,) = summary.inv
    assert row.distributions == D(12)


# --- Vorabpauschale, holdings, venues ----------------------------------------


def test_vorabpauschale_uses_first_and_last_trading_day(
    session, broker, etf, tx_factory, price_factory
):
    tx_factory(broker, etf, TxType.BUY, ts(2024, 6, 3), quantity=D(10), amount_eur=D(1000))
    price_factory(etf, date(2025, 1, 2), D(100))  # 1 January is never a trading day
    price_factory(etf, date(2025, 12, 30), D(110))

    summary = summarize(session, 2025)

    (row,) = summary.inv
    assert row.vorabpauschale == D("17.71")
    assert summary.warnings == []


def test_missing_price_warns_and_leaves_the_vorabpauschale_at_zero(
    session, broker, etf, tx_factory
):
    tx_factory(broker, etf, TxType.BUY, ts(2024, 6, 3), quantity=D(10), amount_eur=D(1000))

    summary = summarize(session, 2025)

    (row,) = summary.inv
    assert row.vorabpauschale == D(0)
    assert any("Vorabpauschale set to 0" in w for w in summary.warnings)


def test_eoy_holdings_and_venues(session, okx, btc, tx_factory, price_factory):
    tx_factory(okx, btc, TxType.DEPOSIT, ts(2025, 1, 2), amount_eur=D(20000))
    tx_factory(okx, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(2), amount_eur=D(20000))
    tx_factory(okx, btc, TxType.SELL, ts(2025, 6, 1), quantity=D(1), amount_eur=D(11200))
    tx_factory(okx, None, TxType.WITHDRAWAL, ts(2025, 7, 1), amount_eur=D(500))
    price_factory(btc, date(2025, 12, 31), D(12000))

    summary = summarize(session, 2025)

    assert summary.eoy_holdings == [("okx-live", "BTC", D(1), D("12000.00"))]
    (venue,) = summary.venues
    assert venue.account == "okx-live"
    assert venue.gross_proceeds == D(11200)
    assert venue.gross_acquisitions == D(20000)
    assert venue.disposals_count == 1
    assert venue.deposits == D(20000)
    assert venue.withdrawals == D(500)


def test_paper_accounts_are_excluded_by_account_ids(session, okx, btc, account_factory, tx_factory):
    paper = account_factory(name="okx-paper", kind=AccountKind.CRYPTO_SPOT, mode=Mode.PAPER)
    tx_factory(paper, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(10000))
    tx_factory(paper, btc, TxType.SELL, ts(2025, 6, 1), quantity=D(1), amount_eur=D(11200))

    assert summarize(session, 2025, [okx.id]).p23.net == D(0)
    assert summarize(session, 2025, [paper.id]).p23.net == D(1200)


def test_disposal_without_a_regime_is_warned_about(session, account_factory, btc, tx_factory):
    # Crypto in a plain broker account is neither spot nor a Termingeschaeft,
    # so `regime_for` yields NONE and the disposal reaches no bucket at all.
    odd = account_factory(name="t212-invest", kind=AccountKind.BROKER_INVEST, mode=Mode.LIVE)
    tx_factory(odd, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(10000))
    sell = tx_factory(odd, btc, TxType.SELL, ts(2025, 6, 1), quantity=D(1), amount_eur=D(11200))

    summary = summarize(session, 2025)

    assert summary.p23.net == D(0)
    assert summary.p20.aktien_gains == D(0)
    assert f"tx {sell.id}: disposal has no tax regime, not counted" in summary.warnings


# --- routes ------------------------------------------------------------------


def test_summary_route_carries_the_disclaimer(client, session, okx, btc, tx_factory):
    _crypto_year(okx, btc, tx_factory, D(11200))

    response = client.get("/api/tax/2025/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["disclaimer"] == "Berechnung — mit Steuerberater prüfen"
    assert body["form_status"] == "Formstand: VZ 2025, geprüft 2026-09-06"
    assert body["p23"]["net"] == "1200"
    assert body["p23"]["exceeded"] is True


def test_years_route_lists_years_with_transactions(client, okx, btc, tx_factory):
    tx_factory(okx, btc, TxType.BUY, ts(2024, 1, 10), quantity=D(1), amount_eur=D(10000))
    tx_factory(okx, btc, TxType.SELL, ts(2025, 6, 1), quantity=D(1), amount_eur=D(11200))

    assert client.get("/api/tax/years").json()["years"] == [2024, 2025]


def test_mode_defaults_to_live(client, account_factory, btc, tx_factory):
    paper = account_factory(name="okx-paper", kind=AccountKind.CRYPTO_SPOT, mode=Mode.PAPER)
    tx_factory(paper, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(10000))
    tx_factory(paper, btc, TxType.SELL, ts(2025, 6, 1), quantity=D(1), amount_eur=D(11200))

    assert client.get("/api/tax/2025/summary").json()["p23"]["net"] == "0"
    assert client.get("/api/tax/2025/summary?mode=all").json()["p23"]["net"] == "1200"


def test_explicit_account_id_is_still_intersected_with_mode(client, account_factory, btc, tx_factory):
    """Naming a paper account under `mode=live` must not pull paper lots into
    a tax figure — the same intersection the stats routes apply.
    """
    paper = account_factory(name="okx-paper2", kind=AccountKind.CRYPTO_SPOT, mode=Mode.PAPER)
    tx_factory(paper, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(10000))

    params = {"account_id": paper.id}
    assert client.get("/api/tax/2025/lots", params=params).json()["lots"] == []
    assert client.get("/api/tax/2025/lots", params={**params, "mode": "paper"}).json()["lots"] != []


def test_disposals_lots_and_export_routes(client, okx, btc, tx_factory):
    _crypto_year(okx, btc, tx_factory, D(11200))
    tx_factory(okx, btc, TxType.BUY, ts(2025, 8, 1), quantity=D(1), amount_eur=D(9000))

    disposals = client.get("/api/tax/2025/disposals").json()["disposals"]
    assert [d["symbol"] for d in disposals] == ["BTC"]
    assert disposals[0]["gain_eur"] == "1200"

    lots = client.get("/api/tax/2025/lots").json()["lots"]
    assert [lot["quantity"] for lot in lots] == ["1"]

    export = client.get("/api/tax/2025/export?format=blockpit")
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    assert "Integration Name" in export.text


def test_lots_carry_the_regime_a_disposal_would_fall_under(
    client, okx, broker, btc, etf, tx_factory
):
    """Only crypto spot lots are § 23 — an ETF lot has no Spekulationsfrist."""
    tx_factory(okx, btc, TxType.BUY, ts(2025, 8, 1), quantity=D(1), amount_eur=D(9000))
    tx_factory(broker, etf, TxType.BUY, ts(2025, 8, 2), quantity=D(10), amount_eur=D(1000))

    lots = client.get("/api/tax/2025/lots?mode=live").json()["lots"]

    assert {lot["symbol"]: lot["regime"] for lot in lots} == {"BTC": "p23", "VWCE": "p20_inv"}


def test_anlage_route_notes_a_year_without_a_line_mapping(client, okx, btc, tx_factory):
    _crypto_year(okx, btc, tx_factory, D(11200))

    assert client.get("/api/tax/2025/anlage").json()["note"] is None
    body = client.get("/api/tax/2024/anlage").json()
    assert body["lines"] == []
    assert body["note"] == "no line mapping for VZ 2024"


def test_year_out_of_range_is_rejected(client):
    assert client.get("/api/tax/1999/summary").status_code == 422


def test_cli_renders_the_year_as_a_table(session, okx, btc, tx_factory, capsys):
    _crypto_year(okx, btc, tx_factory, D(11200))

    print_year(session, 2025)

    out = capsys.readouterr().out
    assert "Steuerjahr 2025" in out
    assert "1.200,00 €" in out
    assert "Gewinn / Verlust" in out  # Anlage SO 51
    assert "1200.00" in out
