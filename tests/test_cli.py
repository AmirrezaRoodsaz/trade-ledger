from __future__ import annotations

from trade_ledger import cli


def test_stub_subcommands_print_not_yet_implemented(capsys):
    for argv in (["sync", "--all"], ["report"]):
        assert cli.main(argv) == 0
        assert "not yet implemented" in capsys.readouterr().out


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
        account = Account(
            venue=Venue.OKX, name="okx-live", kind=AccountKind.CRYPTO_SPOT, mode=Mode.LIVE
        )
        instrument = Instrument(symbol="BTC", asset_class=AssetClass.CRYPTO)
        session.add_all([account, instrument])
        session.flush()
        for tx_type, when, amount in (
            (TxType.BUY, datetime(2025, 1, 10, 12, tzinfo=UTC), Decimal(10000)),
            (TxType.SELL, datetime(2025, 6, 1, 12, tzinfo=UTC), Decimal(11200)),
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
    assert "Steuerjahr 2025" in out
    assert "Anlage SO" in out
    assert "Gewinn / Verlust" in out  # Anlage SO 51
    assert "1200.00" in out
