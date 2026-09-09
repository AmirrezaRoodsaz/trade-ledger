"""What a bot's light shows, and which trades count as its own."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import analytics
from ..enums import BotStatus, RunStatus, TradeStatus
from ..models import Bot, BotRun, BotState, Trade
from .schedule import deadline


def derive_status(
    bot: Bot, state: BotState | None, last_run: BotRun | None, now: datetime
) -> BotStatus:
    """First match wins, in the plan's priority order.

    `stale` outranks `error` on purpose: a bot that stopped reporting may
    well also have a failed last run, and "we have not heard from it" is the
    more useful thing to put in front of the operator.
    """
    if not bot.enabled:
        return BotStatus.DISABLED
    if now > deadline(bot):
        return BotStatus.STALE
    if (last_run is not None and last_run.status == RunStatus.ERROR) or (
        state is not None and state.reconciliation == "mismatch"
    ):
        return BotStatus.ERROR
    if bot.paused_entries:
        return BotStatus.PAUSED
    if last_run is not None and last_run.status == RunStatus.RUNNING:
        return BotStatus.RUNNING
    return BotStatus.OK


def bot_trades(session: Session, bot: Bot) -> list[Trade]:
    """The bot's closed trades, oldest first, ties broken by id.

    `analytics.closed` drops the rows with no risk or no result — an R-less
    trade cannot move a profit factor or count as a loss.
    """
    rows = list(
        session.execute(
            select(Trade)
            .where(Trade.bot_id == bot.id, Trade.status == TradeStatus.CLOSED)
            .order_by(Trade.closed_ts, Trade.id)
        ).scalars()
    )
    return analytics.closed(rows)
