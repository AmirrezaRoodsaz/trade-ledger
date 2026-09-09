"""`trade-bot` — one bot process, one run.

    trade-bot --bot okx-donchian-4h [--dry-run] [--once]
    trade-bot new my_strategy          # scaffold a strategy and an env file

Everything else comes from the environment, so a token or an API key is
never on a command line or in a process listing:

| variable | meaning |
|---|---|
| `TRADE_LEDGER_URL` | where the app answers (default `http://127.0.0.1:8000`) |
| `BOT_TOKEN` | the bot's token, shown once when the bot was created |
| `EXCHANGE_ID` | ccxt id, e.g. `okx` — or `fake` with `--dry-run`, for a smoke test |
| `EXCHANGE_KEY`, `EXCHANGE_SECRET`, `EXCHANGE_PASSPHRASE` | venue credentials |
| `EXCHANGE_DEMO` | `1` for the venue's demo/sandbox, `0` for real money |
| `MARKET_TYPE` | `spot` or `swap` |

Exit codes: `0` when the run finished `ok`, `dry_run` or `skipped`, `1` for
anything else — an unreachable app, a reconciliation mismatch, a venue
error. That is what the systemd unit and the supervisor read.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ..prices.service import Candle
from ..settings import env_values, get_settings
from .client import AppUnreachable, BotClient
from .runner import ReconciliationError, run_once

OK_STATUSES = {"ok", "dry_run", "skipped"}

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "bots" / "template"


class FakeExchange:
    """A venue that does not exist, for smoking out the loop without keys.

    Flat candles, so no strategy ever signals; empty book, so nothing
    reconciles against anything. Every write raises — `--dry-run` is the only
    way this class is reachable, and a dry run must never order.
    """

    market_type = "spot"

    def balances(self) -> dict[str, Decimal]:
        return {"EUR": Decimal(1000)}

    def positions(self) -> list[dict]:
        return []

    def open_orders(self) -> list[dict]:
        return []

    def market_limits(self, symbol: str) -> dict:
        return {"min_qty": Decimal("0.0001"), "step": Decimal("0.0001"), "tick": Decimal("0.01")}

    def candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        start = datetime.now(UTC) - timedelta(hours=4 * limit)
        price = Decimal(100)
        return [
            Candle(
                date=start + timedelta(hours=4 * i),
                open=price,
                high=price,
                low=price,
                close=price,
            )
            for i in range(limit)
        ]

    def _refuse(self, *args, **kwargs):
        raise RuntimeError("the fake exchange never trades")

    place_order = place_stop = cancel_all = close_position = _refuse


def build_exchange():
    """The real wrapper, or the fake one. `botkit.exchange` is imported here
    rather than at module level so `EXCHANGE_ID=fake` needs no ccxt
    credentials — and no ccxt — at all.
    """
    ccxt_id = os.environ.get("EXCHANGE_ID", "okx")
    if ccxt_id == "fake":
        return FakeExchange()

    from .exchange import Exchange

    return Exchange(
        ccxt_id,
        os.environ.get("EXCHANGE_KEY", ""),
        os.environ.get("EXCHANGE_SECRET", ""),
        os.environ.get("EXCHANGE_PASSPHRASE") or None,
        demo=os.environ.get("EXCHANGE_DEMO", "1") == "1",
        market_type=os.environ.get("MARKET_TYPE", "spot"),
    )


def scaffold(name: str, root: Path | None = None) -> int:
    """`trade-bot new <name>`: a strategy module and an env file to fill in.

    Neither file is ever overwritten — one of them holds a token and API keys
    once it is filled in, and the other is somebody's work.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        print(
            f"{name!r} is not a usable name: lower case, starting with a letter, "
            "then letters, digits, '_' or '-'",
            file=sys.stderr,
        )
        return 1

    strategy = (root or REPO_ROOT) / "bots" / "strategies" / f"{name}.py"
    env = bot_env_file(name)
    for target in (strategy, env):
        if target.exists():
            print(f"{target} already exists: refusing to overwrite", file=sys.stderr)
            return 1

    for target, source in ((strategy, "strategy_template.py"), (env, "bot.env.example")):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((TEMPLATE_DIR / source).read_text())
    env.chmod(0o600)

    print(
        f"{strategy}\n"
        f"{env}\n"
        "\nNext:\n"
        f"  1. write the entry and exit rules in {strategy.name}, then run it:\n"
        f"       uv run python {strategy}\n"
        "  2. move it to trade_ledger/botkit/strategies/ and register it in\n"
        f"     STRATEGIES and DEFAULT_PARAMS as \"{name}\"\n"
        "  3. create the bot in the UI (Bots -> New bot) with a preset on that\n"
        "     strategy, and paste the token it shows once into BOT_TOKEN in\n"
        f"     {env}\n"
        f"  4. uv run trade-bot --bot <slug> --dry-run\n"
        "\nThe contract is in bots/BOT_CONTRACT.md."
    )
    return 0


def bot_env_file(slug: str) -> Path:
    """Where the bot's own environment lives — the file `trade-bot new` writes
    and the supervisor hands to the child process."""
    return Path(get_settings().DATA_DIR) / "bots" / slug / ".env"


def load_bot_env(slug: str) -> None:
    """Read `DATA_DIR/bots/<slug>/.env` into the environment, if it is there.

    The supervisor already passes that file to the bots it launches; a bot
    started by hand or by systemd got nothing, so the documented
    "write the env file, then `trade-bot --bot <slug>`" did not actually run.
    Existing variables win, so `EXCHANGE_ID=fake trade-bot ...` still
    overrides the file for a smoke test.
    """
    for key, value in env_values(bot_env_file(slug)).items():
        os.environ.setdefault(key, value)


def main(argv: list[str] | None = None) -> int:
    # ponytail: a leading positional check rather than argparse subparsers —
    # `new` is the only subcommand, and `--bot x --once` must keep parsing
    # exactly as it did.
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "new":
        if len(argv) != 2:
            print("usage: trade-bot new <name>", file=sys.stderr)
            return 1
        return scaffold(argv[1])

    parser = argparse.ArgumentParser(prog="trade-bot", description="Run one bot once.")
    parser.add_argument("--bot", required=True, help="the bot's slug, as in the app")
    parser.add_argument("--dry-run", action="store_true", help="plan and journal, never order")
    parser.add_argument(
        "--once",
        action="store_true",
        default=True,
        help="one pass and exit (the only mode; a scheduler decides when)",
    )
    args = parser.parse_args(argv)
    load_bot_env(args.bot)

    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("BOT_TOKEN is not set: refusing to run", file=sys.stderr)
        return 1
    if os.environ.get("EXCHANGE_ID") == "fake" and not args.dry_run:
        print("EXCHANGE_ID=fake is only allowed with --dry-run", file=sys.stderr)
        return 1

    client = BotClient(
        os.environ.get("TRADE_LEDGER_URL", "http://127.0.0.1:8000"), token, args.bot
    )
    exchange = build_exchange()
    try:
        summary = run_once(client, exchange, dry_run=args.dry_run)
    except (AppUnreachable, ReconciliationError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close = getattr(exchange, "close", None)
        if callable(close):
            close()

    print(f"{args.bot}: {summary}")
    return 0 if summary.get("status") in OK_STATUSES else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
