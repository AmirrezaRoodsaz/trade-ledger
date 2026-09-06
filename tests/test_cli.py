from __future__ import annotations

from trade_ledger import cli


def test_stub_subcommands_print_not_yet_implemented(capsys):
    for argv in (["sync", "--all"], ["tax-year", "2026"], ["report"]):
        assert cli.main(argv) == 0
        assert "not yet implemented" in capsys.readouterr().out


def test_import_reports_missing_importers_module(capsys):
    # Task 2 (the importers package) is a parallel task not yet merged here.
    rc = cli.main(["import", "--account", "acct", "--format", "generic", "file.csv"])
    assert rc == 1
    assert "importers not available" in capsys.readouterr().out


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
