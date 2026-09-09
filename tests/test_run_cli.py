"""`trade-bot`'s argument and environment handling.

`main` returns an exit code; it never raises `SystemExit` of its own, so the
supervisor and the systemd unit read a number rather than a traceback.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trade_ledger.botkit import run as run_module
from trade_ledger.botkit.client import AppUnreachable
from trade_ledger.botkit.runner import ReconciliationError


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "TRADE_LEDGER_URL",
        "BOT_TOKEN",
        "EXCHANGE_ID",
        "EXCHANGE_KEY",
        "EXCHANGE_SECRET",
        "EXCHANGE_PASSPHRASE",
        "EXCHANGE_DEMO",
        "MARKET_TYPE",
    ):
        monkeypatch.delenv(key, raising=False)


def _never_runs(*args, **kwargs):
    raise AssertionError("run_once must not be reached")


def test_a_missing_token_refuses_to_run(monkeypatch, capsys):
    monkeypatch.setattr(run_module, "run_once", _never_runs)
    assert run_module.main(["--bot", "smoke", "--dry-run"]) == 1
    assert "BOT_TOKEN is not set" in capsys.readouterr().err


def test_the_fake_exchange_is_refused_outside_a_dry_run(monkeypatch, capsys):
    monkeypatch.setenv("BOT_TOKEN", "tok")
    monkeypatch.setenv("EXCHANGE_ID", "fake")
    monkeypatch.setattr(run_module, "run_once", _never_runs)

    assert run_module.main(["--bot", "smoke"]) == 1
    assert "only allowed with --dry-run" in capsys.readouterr().err


def test_a_dry_run_against_the_fake_exchange_exits_zero(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "tok")
    monkeypatch.setenv("EXCHANGE_ID", "fake")
    seen = {}

    def fake_run_once(client, exchange, *, dry_run, **kwargs):
        seen["exchange"], seen["dry_run"] = exchange, dry_run
        return {"status": "dry_run"}

    monkeypatch.setattr(run_module, "run_once", fake_run_once)

    assert run_module.main(["--bot", "smoke", "--dry-run"]) == 0
    assert isinstance(seen["exchange"], run_module.FakeExchange)
    assert seen["dry_run"] is True


@pytest.mark.parametrize("status, code", [("ok", 0), ("skipped", 0), ("error", 1)])
def test_the_exit_code_follows_the_run_status(monkeypatch, status, code):
    monkeypatch.setenv("BOT_TOKEN", "tok")
    monkeypatch.setenv("EXCHANGE_ID", "fake")
    monkeypatch.setattr(run_module, "run_once", lambda *a, **k: {"status": status})
    assert run_module.main(["--bot", "smoke", "--dry-run"]) == code


@pytest.mark.parametrize("failure", [AppUnreachable("no app"), ReconciliationError("2 vs 0")])
def test_the_two_refusals_exit_one_rather_than_raise(monkeypatch, capsys, failure):
    monkeypatch.setenv("BOT_TOKEN", "tok")
    monkeypatch.setenv("EXCHANGE_ID", "fake")

    def boom(*args, **kwargs):
        raise failure

    monkeypatch.setattr(run_module, "run_once", boom)

    assert run_module.main(["--bot", "smoke", "--dry-run"]) == 1
    assert type(failure).__name__ in capsys.readouterr().err


def test_the_fake_market_is_flat_and_refuses_every_write():
    exchange = run_module.FakeExchange()
    candles = exchange.candles("BTC/EUR", "4h", 10)

    assert len(candles) == 10
    assert {c.close for c in candles} == {Decimal(100)}  # flat: no strategy signals
    assert exchange.positions() == []
    for write in (exchange.place_order, exchange.cancel_all, exchange.close_position):
        with pytest.raises(RuntimeError, match="never trades"):
            write("BTC/EUR")


def test_the_bots_own_env_file_is_read_and_the_environment_still_wins(monkeypatch, tmp_path):
    """`bots/README.md` says: write `data/bots/<slug>/.env`, then run
    `trade-bot --bot <slug>`. So the CLI has to read that file — only the
    supervisor used to."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    env_file = tmp_path / "bots" / "smoke" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("BOT_TOKEN=from-file\nEXCHANGE_ID=okx\n")
    monkeypatch.setenv("EXCHANGE_ID", "fake")  # an explicit override, as in the README
    seen = {}

    def fake_run_once(client, exchange, *, dry_run, **kwargs):
        seen["auth"], seen["exchange"] = client._headers["Authorization"], exchange
        return {"status": "dry_run"}

    monkeypatch.setattr(run_module, "run_once", fake_run_once)

    assert run_module.main(["--bot", "smoke", "--dry-run"]) == 0
    assert seen["auth"] == "Bearer from-file"
    assert isinstance(seen["exchange"], run_module.FakeExchange)
