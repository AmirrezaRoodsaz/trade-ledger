"""`trade-bot` — one bot process, one run.

    trade-bot --bot okx-donchian-4h [--dry-run] [--once]

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
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ..prices.service import Candle
from .client import AppUnreachable, BotClient
from .runner import ReconciliationError, run_once

OK_STATUSES = {"ok", "dry_run", "skipped"}


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


def build_exchange(dry_run: bool):
    """The real wrapper, or the fake one. Imported here rather than at module
    level so `EXCHANGE_ID=fake` needs no ccxt credentials at all.
    """
    ccxt_id = os.environ.get("EXCHANGE_ID", "okx")
    if ccxt_id == "fake":
        if not dry_run:
            raise SystemExit("EXCHANGE_ID=fake is only allowed with --dry-run")
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


def main(argv: list[str] | None = None) -> int:
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

    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("BOT_TOKEN is not set: refusing to run", file=sys.stderr)
        return 1

    client = BotClient(
        os.environ.get("TRADE_LEDGER_URL", "http://127.0.0.1:8000"), token, args.bot
    )
    exchange = build_exchange(args.dry_run)
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
