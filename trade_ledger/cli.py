"""`trade-ledger` command line entry point.

`serve`, `import`, `export-notes`, `import-notes`, `prices --refresh` and
`tax-year` are implemented here. `sync` and `report` are stubs that print
"not yet implemented" — Tasks 3 and 13 fill them in; this file just
gives the CLI surface its final shape now so those tasks only add a
function body, not a new subcommand.
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
# manual refresh just re-pulls a rolling window. Narrow this once Task 5 (or
# a later one) tracks a per-instrument high-water mark.
_PRICE_REFRESH_LOOKBACK = timedelta(days=30)


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .main import create_app

    settings = get_settings()
    uvicorn.run(create_app(), host=settings.HOST, port=settings.PORT)
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    print("sync: not yet implemented")
    return 0


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

    settings = get_settings()
    db.init_db(settings.DB_PATH)
    out_dir = args.dir or settings.NOTES_OUT_DIR
    with db.SessionLocal() as session:
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
    print("report: not yet implemented")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trade-ledger")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve").set_defaults(func=_cmd_serve)

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
    p_report.add_argument("--week", nargs="?", default=None)
    p_report.set_defaults(func=_cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
