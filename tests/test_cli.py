from __future__ import annotations

import os
import subprocess
import sys

from trade_ledger import cli


def test_serve_parses_an_explicit_port_of_zero(capsys):
    """`--port 0` is a real value, not a missing one: `or settings.PORT` would
    have swallowed it."""
    args = cli.build_parser().parse_args(["serve", "--port", "0", "--no-browser"])

    assert args.port == 0
    assert args.no_browser is True


def test_sync_all_on_an_empty_ledger_is_a_no_op(capsys, monkeypatch):
    monkeypatch.setenv("DB_PATH", ":memory:")
    assert cli.main(["sync", "--all"]) == 0
    assert "no accounts to sync" in capsys.readouterr().out


def test_sync_names_an_account_it_does_not_know(capsys, monkeypatch):
    monkeypatch.setenv("DB_PATH", ":memory:")
    assert cli.main(["sync", "--account", "nope"]) == 1
    assert "unknown account: nope" in capsys.readouterr().out


def test_report_command_runs_in_a_fresh_process(tmp_path):
    """Regression: `reports/weekly.py` used to import `api._common` at module
    load time. That import pulls in `api/__init__`'s router auto-discovery,
    which imports `api/reports.py`, which imports `build_weekly` back out of
    the still-initializing `reports.weekly` module — a circular import. It
    never showed up under pytest, where `trade_ledger.api` is already fully
    imported (by the `client` fixture elsewhere) before anything imports
    `reports.weekly` — only a fresh process running `report` first hit it.
    """
    env = {**os.environ, "DB_PATH": ":memory:", "DATA_DIR": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "trade_ledger.cli", "report", "--week", "2026-09-06"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "exports" / "reports" / "weekly-2026-09-06-paper.pdf").is_file()


def test_import_reports_unknown_format(capsys):
    rc = cli.main(["import", "--account", "acct", "--format", "bogus", "file.csv"])
    assert rc == 1
    assert "unknown import format" in capsys.readouterr().out


def test_import_reports_unknown_account(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DB_PATH", ":memory:")
    csv_file = tmp_path / "file.csv"
    csv_file.write_text(
        "date,type,symbol,asset_class,quantity,price,currency,fee,fee_currency,"
        "amount_eur,external_id,note\n"
    )
    rc = cli.main(["import", "--account", "does-not-exist", "--format", "generic", str(csv_file)])
    assert rc == 1
    assert "unknown account: does-not-exist" in capsys.readouterr().out


def test_prices_without_refresh_is_a_noop(capsys):
    assert cli.main(["prices"]) == 0
    assert "nothing to do" in capsys.readouterr().out


def test_export_notes_writes_no_files_against_an_empty_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("NOTES_OUT_DIR", str(tmp_path))
    assert cli.main(["export-notes", "--dir", str(tmp_path)]) == 0
    assert list(tmp_path.glob("*.md")) == []


def test_build_parser_requires_a_subcommand():
    import pytest

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_tax_year_prints_the_anlage_lines(tmp_path, capsys, monkeypatch):
    from datetime import UTC, datetime
    from decimal import Decimal

    from trade_ledger import db
    from trade_ledger.enums import AccountKind, AssetClass, Mode, TxSource, TxType, Venue
    from trade_ledger.models import Account, Instrument, Transaction

    monkeypatch.setenv("DB_PATH", str(tmp_path / "ledger.db"))
    db.init_db(tmp_path / "ledger.db")
    with db.SessionLocal() as session:
        live = Account(
            venue=Venue.OKX, name="okx-live", kind=AccountKind.CRYPTO_SPOT, mode=Mode.LIVE
        )
        paper = Account(
            venue=Venue.OKX, name="okx-paper", kind=AccountKind.CRYPTO_SPOT, mode=Mode.PAPER
        )
        instrument = Instrument(symbol="BTC", asset_class=AssetClass.CRYPTO)
        session.add_all([live, paper, instrument])
        session.flush()
        for account, proceeds in ((live, Decimal(11200)), (paper, Decimal(17700))):
            for tx_type, when, amount in (
                (TxType.BUY, datetime(2025, 1, 10, 12, tzinfo=UTC), Decimal(10000)),
                (TxType.SELL, datetime(2025, 6, 1, 12, tzinfo=UTC), proceeds),
            ):
                session.add(
                    Transaction(
                        account_id=account.id,
                        instrument_id=instrument.id,
                        type=tx_type,
                        ts=when,
                        quantity=Decimal(1),
                        amount_eur=amount,
                        source=TxSource.MANUAL,
                    )
                )
        session.commit()

    assert cli.main(["tax-year", "2025"]) == 0

    out = capsys.readouterr().out
    assert "Steuerjahr 2025 (Modus: live)" in out
    assert "Anlage SO" in out
    assert "Gewinn / Verlust" in out  # Anlage SO 51
    assert "1200.00" in out
    assert "7700" not in out  # the paper account stays out of the tax figures

    assert cli.main(["tax-year", "2025", "--mode", "all"]) == 0
    assert "8900.00" in capsys.readouterr().out  # 1.200 live + 7.700 paper
