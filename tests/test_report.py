"""Weekly PDF report: `fmt.py` formatting, `build_weekly` against the
analytics fixture, and the `/api/reports` routes.
"""

from __future__ import annotations

import os
import tempfile

# Matplotlib's font-cache build message is noise on a clean test run; give it
# its own throwaway cache dir before `reports.weekly` (which imports
# matplotlib and sets the `Agg` backend) is imported anywhere.
os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp())

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from trade_ledger.enums import AssetClass, Direction, Mode, TradeStatus, TxSource, TxType
from trade_ledger.models import Trade, Transaction
from trade_ledger.reports.fmt import fmt_date, fmt_eur, fmt_pct, fmt_r
from trade_ledger.reports.weekly import (
    _bar,
    _open_crypto_lots,
    _open_trades,
    _tax_meters,
    build_weekly,
)

RISK = Decimal(50)


def _trade(session, account, instrument, r, closed_ts):
    """A closed trade on `closed_ts`, R = `r`, risk 50 — the brief's 6-trade fixture."""
    r = Decimal(r)
    trade = Trade(
        account_id=account.id,
        instrument_id=instrument.id,
        direction=Direction.LONG,
        status=TradeStatus.CLOSED,
        risk_eur=RISK,
        result_eur=r * RISK,
        opened_ts=closed_ts - timedelta(hours=5),
        closed_ts=closed_ts,
    )
    session.add(trade)
    session.flush()
    return trade


def _fixture(session, account, instrument) -> list[Trade]:
    rs = ["2", "-1", "3", "-1", "-1", "0.5"]
    base = datetime(2026, 9, 1, 12, tzinfo=UTC)  # a Tuesday
    return [_trade(session, account, instrument, r, base + timedelta(days=i)) for i, r in enumerate(rs)]


# --- fmt.py --------------------------------------------------------------


def test_fmt_eur_de_de():
    assert fmt_eur(Decimal("1234.5")) == "1.234,50 €"


def test_fmt_r_signed():
    assert fmt_r(Decimal("2.5")) == "+2,50 R"
    assert fmt_r(Decimal(-1)) == "-1,00 R"


def test_fmt_pct_one_decimal():
    assert fmt_pct(Decimal("0.55")) == "55,0 %"


def test_fmt_date_de_de():
    assert fmt_date(date(2026, 9, 6)) == "06.09.2026"


# --- build_weekly ----------------------------------------------------------


def test_build_weekly_writes_a_real_pdf(tmp_path, monkeypatch, session, account_factory, instrument_factory):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    account = account_factory()
    instrument = instrument_factory()
    _fixture(session, account, instrument)
    session.commit()

    path = build_weekly(session, date(2026, 9, 6), "paper")

    assert path == tmp_path / "exports" / "reports" / "weekly-2026-09-06-paper.pdf"
    assert path.is_file()
    assert path.stat().st_size > 5_000


def test_build_weekly_on_empty_db_still_produces_a_pdf(tmp_path, monkeypatch, session):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    path = build_weekly(session, date(2026, 9, 6), "paper")
    assert path.is_file()
    assert path.stat().st_size > 1_000


# --- populated open trades / open lots / tax meters ------------------------


@pytest.fixture()
def live_and_paper(session, account_factory, instrument_factory):
    """One live account holding a crypto lot bought well over 12 months
    before the report's `week_end`, and one open trade on a paper account.
    """
    live = account_factory(mode=Mode.LIVE)
    paper = account_factory(mode=Mode.PAPER)
    crypto = instrument_factory(symbol="BTC", asset_class=AssetClass.CRYPTO)

    session.add(
        Transaction(
            account_id=live.id,
            instrument_id=crypto.id,
            type=TxType.BUY,
            ts=datetime(2024, 1, 1, 12, tzinfo=UTC),
            quantity=Decimal("0.1"),
            amount_eur=Decimal(4000),
            source=TxSource.MANUAL,
        )
    )
    open_trade = Trade(
        account_id=paper.id,
        instrument_id=crypto.id,
        direction=Direction.LONG,
        status=TradeStatus.OPEN,
        risk_eur=Decimal(50),
        planned_entry=Decimal(100),
        planned_stop=Decimal(95),
        opened_ts=datetime(2026, 9, 1, 10, tzinfo=UTC),
    )
    session.add(open_trade)
    session.commit()
    return {"live": live, "paper": paper, "crypto": crypto, "open_trade": open_trade}


def test_open_trades_reads_symbol_and_planned_fields(session, live_and_paper):
    rows = _open_trades(session, "paper")

    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "BTC"
    assert row["direction"] == "long"
    assert row["entry"] == Decimal(100)  # not yet opened -> falls back to planned_entry
    assert row["stop"] == Decimal(95)
    assert row["risk"] == Decimal(50)
    assert row["ref"] == f"#{live_and_paper['open_trade'].id}"


def test_open_trades_mode_filter_excludes_other_modes(session, live_and_paper):
    assert _open_trades(session, "live") == []


def test_open_crypto_lots_days_to_12m_negative_for_an_old_lot(session, live_and_paper):
    rows = _open_crypto_lots(session, date(2026, 9, 6))

    assert len(rows) == 1
    lot = rows[0]
    assert lot["symbol"] == "BTC"
    assert lot["qty"] == Decimal("0.1")
    assert lot["acquired"] == date(2024, 1, 1)
    # Bought > 12 months before week_end -> `_open_lots_table` renders "steuerfrei".
    assert lot["days_to_12m"] < 0


def test_tax_meters_reads_live_accounts_only(session, live_and_paper):
    tax = _tax_meters(session, 2026)

    assert tax["p23_limit"] == Decimal(1000)
    assert tax["p20_limit"] == Decimal(1000)
    assert tax["p23_used"] >= Decimal(0)
    assert tax["p20_used"] >= Decimal(0)


def test_bar_renders_a_20_wide_ascii_gauge():
    assert _bar(Decimal(0), Decimal(1000)) == "[--------------------] 0,0 %"
    assert _bar(Decimal(500), Decimal(1000)) == "[##########----------] 50,0 %"
    assert _bar(Decimal(1000), Decimal(1000)) == "[####################] 100,0 %"
    assert _bar(Decimal(1500), Decimal(1000)) == "[####################] 100,0 %"  # capped


# --- API ---------------------------------------------------------------


def test_weekly_route_builds_and_downloads(tmp_path, monkeypatch, client, account_factory, instrument_factory, session):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    account = account_factory()
    instrument = instrument_factory()
    _fixture(session, account, instrument)
    session.commit()

    resp = client.post("/api/reports/weekly", json={"week_end": "2026-09-06", "mode": "paper"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "weekly-2026-09-06-paper.pdf"
    assert body["url"] == "/api/reports/weekly-2026-09-06-paper.pdf"

    listing = client.get("/api/reports").json()
    assert any(row["name"] == body["name"] for row in listing)

    download = client.get(body["url"])
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    assert len(download.content) > 5_000


def test_weekly_route_rejects_bad_mode(client):
    resp = client.post("/api/reports/weekly", json={"mode": "bogus"})
    assert resp.status_code == 422


def test_report_name_traversal_is_rejected(client):
    assert client.get("/api/reports/..%2F..%2Fetc%2Fpasswd").status_code in (404, 422)
    assert client.get("/api/reports/not-a-report.pdf").status_code == 422
    assert client.get("/api/reports/weekly-2026-09-06-paper.pdf").status_code == 404
