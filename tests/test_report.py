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

from trade_ledger.enums import Direction, TradeStatus
from trade_ledger.models import Trade
from trade_ledger.reports.fmt import fmt_date, fmt_eur, fmt_pct, fmt_r
from trade_ledger.reports.weekly import build_weekly

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
