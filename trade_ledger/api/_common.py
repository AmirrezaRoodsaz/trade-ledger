"""Small helpers shared by more than one router. Not a router itself — the
leading underscore also keeps `api.ROUTERS` auto-discovery from mistaking it
for one (it has no `router` attribute anyway, but the name makes intent clear).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from ..enums import Mode
from ..models import Account, Bot, Instrument, PlaybookVersion, Trade

ModeFilter = Literal["live", "paper", "demo", "all"]


def get_account_or_404(session: Session, account_id: int) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    return account


def get_bot_or_404(session: Session, slug: str) -> Bot:
    bot = session.execute(select(Bot).where(Bot.slug == slug)).scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="bot not found")
    return bot


def get_instrument_or_404(session: Session, instrument_id: int) -> Instrument:
    instrument = session.get(Instrument, instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    return instrument


def resolve_account_ids(session: Session, account_id: list[int] | None, mode: str) -> list[int]:
    """Which accounts a stats endpoint reads.

    Modes are never mixed. `mode` filters the result even when `account_id`
    names accounts explicitly, so an id belonging to another mode yields no
    accounts rather than leaking a live position into a paper figure — the
    same rule `mode_filter` applies to trades. UI account tabs pass
    `mode=all`, which uses the given ids as-is.

    An id that matches no account at all is still a 404: a typo should not
    read as an empty portfolio.
    """
    ids = [get_account_or_404(session, one).id for one in account_id or []]
    if mode == "all":
        return ids or list(session.execute(select(Account.id)).scalars())
    stmt = select(Account.id).where(Account.mode == mode)
    if ids:
        stmt = stmt.where(Account.id.in_(ids))
    return list(session.execute(stmt).scalars())


def mode_filter(stmt: Select, mode: str) -> Select:
    """Trades never mix modes unless `mode=all` is explicit. A trade's mode
    is its account's.
    """
    if mode == "all":
        return stmt
    if mode not in set(Mode):
        raise HTTPException(status_code=422, detail=f"unknown mode: {mode}")
    return stmt.where(Trade.account_id.in_(select(Account.id).where(Account.mode == mode)))


def trade_filters(
    stmt: Select,
    *,
    mode: str = "paper",
    account_id: list[int] | None = None,
    playbook_id: int | None = None,
    instrument_id: int | None = None,
    tag: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Select:
    """The common filter set shared by `/trades` and every `/analytics`
    endpoint: mode, account, playbook, instrument, tag and an opened-date
    range.
    """
    stmt = mode_filter(stmt, mode)
    if account_id:
        stmt = stmt.where(Trade.account_id.in_(account_id))
    if playbook_id is not None:
        stmt = stmt.where(
            Trade.playbook_version_id.in_(
                select(PlaybookVersion.id).where(PlaybookVersion.playbook_id == playbook_id)
            )
        )
    if instrument_id is not None:
        stmt = stmt.where(Trade.instrument_id == instrument_id)
    if tag is not None:
        # ponytail: tags are a JSON list in a text column; a LIKE on the
        # quoted name is exact enough. Move to a join table if tags ever need
        # renaming or counting.
        stmt = stmt.where(Trade.tags.like(f'%"{tag}"%'))
    if date_from is not None:
        stmt = stmt.where(Trade.opened_ts >= date_from)
    if date_to is not None:
        stmt = stmt.where(Trade.opened_ts <= date_to)
    return stmt
