"""Adapter protocol, credential lookup, and per-venue dispatch shared by
every read-only venue integration: Trading 212 (`trading212.py`) and
OKX/Kraken via `ccxt` (`ccxt_adapter.py`).
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
from ..prices.service import fill_pending_eur
from ..settings import Settings, env_values


class MissingCredentialsError(Exception):
    """Raised when an account's `{prefix}_API_KEY`/`{prefix}_API_SECRET` are absent."""


class Adapter(Protocol):
    warnings: list[str]

    def fetch_transactions(self, since: datetime | None) -> list[TxDraft]: ...
    def fetch_balances(self) -> dict[str, Decimal]: ...


def credentials(prefix: str) -> dict[str, str]:
    """Read `{prefix}_API_KEY`/`{prefix}_API_SECRET`/`{prefix}_API_PASSPHRASE`:
    `os.environ` first, falling back to `.env` (parsed the same way
    `settings.env_status()` does). Never logs or returns anything beyond
    these keys — a missing key is simply absent from the result, never an
    empty string.

    Passphrase is only ever required by OKX (`build_adapter`'s OKX branch is
    the only reader of `f"{prefix}_API_PASSPHRASE"`). It's still looked up
    here unconditionally rather than gated on venue — one shared read is
    simpler than plumbing venue into this helper, and every other venue's
    `build_adapter` branch just never reads the key back out of the dict it
    gets, so an unused `_API_PASSPHRASE` env var for e.g. a Trading 212 or
    Kraken account is harmless.
    """
    from_file = env_values()
    result: dict[str, str] = {}
    for suffix in ("API_KEY", "API_SECRET", "API_PASSPHRASE"):
        key = f"{prefix}_{suffix}"
        value = os.environ.get(key) or from_file.get(key)
        if value:
            result[key] = value
    return result


def build_adapter(account: Account, settings: Settings) -> Adapter:
    """Dispatch on `account.venue`. Trading 212, OKX, and Kraken are wired
    up; every other venue raises `NotImplementedError`.
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

    if account.venue in (Venue.OKX, Venue.KRAKEN):
        from .ccxt_adapter import CcxtAdapter

        prefix = account.credential_env_prefix
        creds = credentials(prefix) if prefix else {}
        api_key = creds.get(f"{prefix}_API_KEY")
        api_secret = creds.get(f"{prefix}_API_SECRET")
        if not api_key or not api_secret:
            raise MissingCredentialsError("missing credentials")

        if account.venue == Venue.OKX:
            passphrase = creds.get(f"{prefix}_API_PASSPHRASE")
            if not passphrase:
                raise MissingCredentialsError("missing credentials")
            return CcxtAdapter(
                "okx", api_key, api_secret, passphrase, demo=account.mode == Mode.DEMO
            )

        # Kraken has no sandbox/demo mode — always live, regardless of our
        # own `mode` field (a paper/demo Kraken account just means "don't
        # trade on it", not "hit a sandbox API that doesn't exist").
        return CcxtAdapter("kraken", api_key, api_secret)

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
    except (MissingCredentialsError, NotImplementedError) as exc:
        # NotImplementedError: an unsupported venue (e.g. OKX/Kraken before
        # their adapter lands) — same "can't sync" outcome as missing creds,
        # so the run still finishes as `error` instead of raw-500ing the API.
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

    # Newly imported non-EUR rows land `fx_source="pending"`; resolve what the
    # cached rates/closes already cover before the run is called done.
    # ponytail: not an attribute on `SyncRun` — the count is interesting to
    # the caller of this one sync, not worth a column and a migration.
    run.filled_pending = fill_pending_eur(session, [account.id])

    run.status = "ok"
    run.added = added
    run.skipped = skipped
    warnings = getattr(adapter, "warnings", [])
    if warnings:
        run.error = "; ".join(warnings)
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
