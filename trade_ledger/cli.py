"""`trade-ledger` command line entry point.

`serve`, `import`, `export-notes`, `import-notes`, `prices --refresh`,
`tax-year`, `report` and `sync` are implemented here.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from . import db
from .settings import get_settings

# ponytail: instruments have no "last price fetched" bookkeeping yet, so a
# manual refresh just re-pulls a rolling window. Narrow this once instruments
# carry a per-instrument high-water mark.
_PRICE_REFRESH_LOOKBACK = timedelta(days=30)


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .main import create_app

    settings = get_settings()
    port = args.port if args.port is not None else settings.PORT
    url = f"http://{settings.HOST}:{port}"
    if not args.no_browser:
        import threading
        import webbrowser

        # ponytail: a timer rather than a uvicorn startup hook — uvicorn.run
        # blocks, and one second is long enough for the socket to be up.
        threading.Timer(1.0, webbrowser.open, [url]).start()
    print(f"trade-ledger on {url}")
    uvicorn.run(create_app(background=True), host=settings.HOST, port=port)
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    from .adapters.base import sync_account
    from .models import Account

    settings = get_settings()
    db.init_db(settings.DB_PATH)
    with db.SessionLocal() as session:
        stmt = select(Account)
        if not args.all:
            stmt = stmt.where(Account.name == args.account)
        accounts = list(session.execute(stmt).scalars())
        if not accounts:
            print("no accounts to sync" if args.all else f"unknown account: {args.account}")
            return 0 if args.all else 1
        failed = False
        for account in accounts:
            run = sync_account(session, account, settings)
            detail = f" ({run.error})" if run.error else ""
            print(f"{account.name}: {run.status}, added {run.added}, skipped {run.skipped}{detail}")
            failed = failed or run.status != "ok"
    return 1 if failed else 0


def _cmd_import(args: argparse.Namespace) -> int:
    try:
        from .importers import IMPORTERS
    except ImportError:
        print("importers not available")
        return 1

    importer = IMPORTERS.get(args.format)
    if importer is None:
        print(f"unknown import format: {args.format}")
        return 1

    from .ledger import upsert_transactions
    from .models import Account

    db.init_db(get_settings().DB_PATH)
    with db.SessionLocal() as session:
        account = session.execute(
            select(Account).where(Account.name == args.account)
        ).scalar_one_or_none()
        if account is None:
            print(f"unknown account: {args.account}")
            return 1
        result = importer(Path(args.file).read_bytes())
        added, skipped = upsert_transactions(session, account, result.drafts)
        print(f"added {added}, skipped {skipped}, {len(result.errors)} error(s)")
    return 0


def _cmd_export_notes(args: argparse.Namespace) -> int:
    from . import notes

    db.init_db(get_settings().DB_PATH)
    with db.SessionLocal() as session:
        out_dir = args.dir or notes.default_out_dir(session)
        files = notes.export_all(session, out_dir)
    print(f"exported {len(files)} note(s) to {out_dir}")
    return 0


def _cmd_import_notes(args: argparse.Namespace) -> int:
    from . import notes
    from .models import Account

    db.init_db(get_settings().DB_PATH)
    with db.SessionLocal() as session:
        account = session.execute(
            select(Account).where(Account.name == args.account)
        ).scalar_one_or_none()
        if account is None:
            print(f"unknown account: {args.account}")
            return 1
        created, updated, skipped = notes.import_dir(session, args.dir, account.id)
    print(f"created {created}, updated {updated}, skipped {skipped}")
    return 0


def _cmd_prices(args: argparse.Namespace) -> int:
    if not args.refresh:
        print("prices: nothing to do (pass --refresh)")
        return 0

    from .models import Instrument
    from .prices import service

    db.init_db(get_settings().DB_PATH)
    end = datetime.now(UTC).date()
    start = end - _PRICE_REFRESH_LOOKBACK
    with db.SessionLocal() as session:
        instruments = (
            session.execute(select(Instrument).where(Instrument.price_source.is_not(None)))
            .scalars()
            .all()
        )
        for instrument in instruments:
            service.ensure_prices(session, instrument, start, end)
    print(f"refreshed prices for {len(instruments)} instrument(s)")
    return 0


def _cmd_tax_year(args: argparse.Namespace) -> int:
    from .tax.cli import print_year

    db.init_db(get_settings().DB_PATH)
    with db.SessionLocal() as session:
        print_year(session, args.year, args.mode)
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from datetime import date

    from .reports.weekly import build_weekly

    week_end = date.fromisoformat(args.week) if args.week else datetime.now(UTC).date()
    db.init_db(get_settings().DB_PATH)
    with db.SessionLocal() as session:
        path = build_weekly(session, week_end, args.mode)
    print(str(path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trade-ledger")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--port", type=int, default=None, help="override PORT from .env")
    p_serve.add_argument("--no-browser", action="store_true", help="do not open a browser")
    p_serve.set_defaults(func=_cmd_serve)

    p_sync = sub.add_parser("sync")
    account_group = p_sync.add_mutually_exclusive_group(required=True)
    account_group.add_argument("--account")
    account_group.add_argument("--all", action="store_true")
    p_sync.set_defaults(func=_cmd_sync)

    p_import = sub.add_parser("import")
    p_import.add_argument("--account", required=True)
    p_import.add_argument("--format", required=True)
    p_import.add_argument("file")
    p_import.set_defaults(func=_cmd_import)

    p_export_notes = sub.add_parser("export-notes")
    p_export_notes.add_argument("--dir")
    p_export_notes.set_defaults(func=_cmd_export_notes)

    p_import_notes = sub.add_parser("import-notes")
    p_import_notes.add_argument("--dir", required=True)
    p_import_notes.add_argument("--account", required=True)
    p_import_notes.set_defaults(func=_cmd_import_notes)

    p_prices = sub.add_parser("prices")
    p_prices.add_argument("--refresh", action="store_true")
    p_prices.set_defaults(func=_cmd_prices)

    p_tax_year = sub.add_parser("tax-year")
    p_tax_year.add_argument("year", type=int)
    p_tax_year.add_argument("--mode", choices=["live", "paper", "demo", "all"], default="live")
    p_tax_year.set_defaults(func=_cmd_tax_year)

    p_report = sub.add_parser("report")
    p_report.add_argument("--week", nargs="?", default=None, help="week-end date, ISO format, default today")
    p_report.add_argument("--mode", choices=["live", "paper", "demo", "all"], default="paper")
    p_report.set_defaults(func=_cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
