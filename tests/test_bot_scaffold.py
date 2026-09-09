"""`trade-bot new`, and the contract it points the new bot's author at.

The contract is documentation, so the only thing worth asserting about it is
that it has not fallen behind the code: every push endpoint and every command
kind a bot can be handed must be named in it.
"""

from __future__ import annotations

import re
import runpy
from pathlib import Path

import pytest

from trade_ledger.botkit import run as run_module
from trade_ledger.enums import CommandKind

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (REPO_ROOT / "bots" / "BOT_CONTRACT.md").read_text()


@pytest.fixture()
def scaffold(tmp_path, monkeypatch):
    """`scaffold(name)` writing into `tmp_path` instead of the repo."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return lambda name: run_module.scaffold(name, root=tmp_path)


def _paths(name: str, tmp_path: Path) -> tuple[Path, Path]:
    return (
        tmp_path / "bots" / "strategies" / f"{name}.py",
        tmp_path / "data" / "bots" / name / ".env",
    )


def test_new_writes_a_strategy_and_an_env_file(scaffold, tmp_path, capsys):
    assert scaffold("my_strategy") == 0
    strategy, env = _paths("my_strategy", tmp_path)

    assert strategy.read_text() == (
        REPO_ROOT / "bots" / "template" / "strategy_template.py"
    ).read_text()
    assert env.read_text() == (REPO_ROOT / "bots" / "template" / "bot.env.example").read_text()
    assert env.stat().st_mode & 0o777 == 0o600, "the env file holds a token and API keys"

    out = capsys.readouterr().out
    assert "STRATEGIES" in out and "BOT_TOKEN" in out and "BOT_CONTRACT.md" in out


def test_new_refuses_to_overwrite_either_file(scaffold, tmp_path, capsys):
    assert scaffold("my_strategy") == 0
    strategy, env = _paths("my_strategy", tmp_path)
    env.write_text("BOT_TOKEN=the-real-one\n")

    assert scaffold("my_strategy") == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert env.read_text() == "BOT_TOKEN=the-real-one\n", "the filled-in token survived"

    strategy.unlink()
    assert scaffold("my_strategy") == 1, "the env file alone is enough to refuse"
    assert not strategy.exists()


def test_a_name_that_is_not_a_name_is_refused(scaffold, tmp_path, capsys):
    assert scaffold("../../etc/passwd") == 1
    assert "not a usable name" in capsys.readouterr().err
    assert not (tmp_path / "bots").exists()


def test_new_needs_exactly_one_name(capsys):
    assert run_module.main(["new"]) == 1
    assert "usage: trade-bot new <name>" in capsys.readouterr().err


def test_the_run_flags_still_parse_the_way_they_did(monkeypatch, capsys):
    """`new` is a leading positional, so `--bot x --once` is untouched."""
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    assert run_module.main(["--bot", "smoke", "--once", "--dry-run"]) == 1
    assert "BOT_TOKEN is not set" in capsys.readouterr().err


def test_the_contract_names_every_push_endpoint():
    source = (REPO_ROOT / "trade_ledger" / "api" / "bot_push.py").read_text()
    routes = re.findall(r'@router\.\w+\(\s*"([^"]+)"', source)
    assert routes, "no routes found: the grep is out of date, not the contract"
    missing = [route for route in routes if route not in CONTRACT]
    assert not missing, f"BOT_CONTRACT.md does not document {missing}"


def test_the_contract_names_every_command_kind():
    missing = [kind for kind in CommandKind if f"`{kind}`" not in CONTRACT]
    assert not missing, f"BOT_CONTRACT.md does not document {missing}"


def test_the_contract_states_the_client_id_derivations_the_code_uses():
    """The rule the review caught missing: three orders, three ids. If either
    derivation moves in the code, this says the document has to move too.
    """
    exchange = (REPO_ROOT / "trade_ledger" / "botkit" / "exchange.py").read_text()
    runner = (REPO_ROOT / "trade_ledger" / "botkit" / "runner.py").read_text()
    assert 'f"{client_id[:29]}sl"' in exchange, "the stop's id rule moved"
    assert 'f"{entry_ref[:31]}x"' in runner, "the exit's id rule moved"
    assert 'external_ref[:29] + "sl"' in CONTRACT
    assert 'external_ref[:31] + "x"' in CONTRACT


def test_the_template_self_check_runs_clean(capsys):
    runpy.run_path(
        str(REPO_ROOT / "bots" / "template" / "strategy_template.py"), run_name="__main__"
    )
    assert capsys.readouterr().out.startswith("ok:")
