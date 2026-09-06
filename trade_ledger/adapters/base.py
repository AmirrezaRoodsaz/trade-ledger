"""Adapter protocol, credential lookup, and per-venue dispatch shared by
every read-only venue integration. Trading 212 is wired up now; OKX/Kraken
land in a later task's `ccxt_adapter.py`.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import Mode, Venue
from ..ledger import TxDraft, upsert_transactions
from ..models import Account, SyncRun
from ..settings import _ENV_FILE, Settings, _parse_env_file


class MissingCredentialsError(Exception):
    """Raised when an account's `{prefix}_API_KEY`/`{prefix}_API_SECRET` are absent."""


class Adapter(Protocol):
    def fetch_transactions(self, since: datetime | None) -> list[TxDraft]: ...
    def fetch_balances(self) -> dict[str, Decimal]: ...


def credentials(prefix: str) -> dict[str, str]:
    """Read `{prefix}_API_KEY`/`{prefix}_API_SECRET`: `os.environ` first,
    falling back to `.env` (parsed the same way `settings.env_status()`
    does). Never logs or returns anything beyond these two keys — a missing
    key is simply absent from the result, never an empty string.
    """
    from_file = _parse_env_file(_ENV_FILE)
    result: dict[str, str] = {}
    for suffix in ("API_KEY", "API_SECRET"):
        key = f"{prefix}_{suffix}"
        value = os.environ.get(key) or from_file.get(key)
        if value:
            result[key] = value
    return result


def build_adapter(account: Account, settings: Settings) -> Adapter:
    """Dispatch on `account.venue`. Only Trading 212 exists so far; every
    other venue raises until a later task adds it.
    """
    if account.venue == Venue.TRADING212:
        from .trading212 import Trading212Adapter

        prefix = account.credential_env_prefix
        creds = credentials(prefix) if prefix else {}
        api_key = creds.get(f"{prefix}_API_KEY")
        api_secret = creds.get(f"{prefix}_API_SECRET")
        if not api_key or not api_secret:
            raise MissingCredentialsError("missing credentials")
        # ponytail: our own `mode` (live/paper/demo) decides which T212 API
        # environment to call — only a `live` account talks to real money.
        return Trading212Adapter(api_key, api_secret, demo=account.mode != Mode.LIVE)

    raise NotImplementedError(f"adapter for {account.venue} not available")


def sync_account(session: Session, account: Account, settings: Settings) -> SyncRun:
    """Create a `SyncRun`, fetch transactions since the last ok run (minus a
    7-day overlap to catch late-settling items), upsert them, and record the
    outcome.

    Never partially commits: `upsert_transactions` does its inserts and its
    one commit atomically, so a failure before that commit rolls back to
    zero new rows; a failure never leaves the `SyncRun` itself half-written
    either — it ends as `error` with a message, or `ok` with counts.
    """
    run = SyncRun(account_id=account.id, status="running")
    session.add(run)
    session.commit()
    session.refresh(run)

    try:
        adapter = build_adapter(account, settings)
    except MissingCredentialsError as exc:
        run.status = "error"
        run.error = str(exc)
        run.finished = datetime.now(UTC)
        session.commit()
        return run

    since = _since(session, account)
    try:
        drafts = adapter.fetch_transactions(since)
        added, skipped = upsert_transactions(session, account, drafts)
    except Exception as exc:  # noqa: BLE001 - any adapter failure (HTTP, parsing, ...) ends the run, not the process
        session.rollback()
        run.status = "error"
        run.error = str(exc)
        run.finished = datetime.now(UTC)
        session.commit()
        return run
    finally:
        close = getattr(adapter, "close", None)
        if callable(close):
            close()

    run.status = "ok"
    run.added = added
    run.skipped = skipped
    run.finished = datetime.now(UTC)
    session.commit()
    return run


def _since(session: Session, account: Account) -> datetime | None:
    last_ok = session.execute(
        select(SyncRun.started)
        .where(SyncRun.account_id == account.id, SyncRun.status == "ok")
        .order_by(SyncRun.started.desc())
        .limit(1)
    ).scalar_one_or_none()
    if last_ok is None:
        return None
    return last_ok - timedelta(days=7)
