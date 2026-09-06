"""Trade lifecycle: plan -> open -> close -> review (or cancel), plus the
numbers derived from the fills linked to a trade.

A "fill" is just a `Transaction` of type BUY/SELL whose `trade_id` points at
the trade. Fills come either from a synced/imported transaction the user
links by id, or from a `ManualFill` (paper trading, where no venue row
exists). Every number on the trade is derived — `recompute` is the only
writer of `avg_entry`, `avg_exit`, `quantity`, `fees_eur` and `result_eur`.

Guard violations raise `StatusError` (the API maps it to 409); bad input
raises `ValueError` (422).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from .enums import Direction, TradeStatus, TxSource, TxType
from .models import Trade, Transaction

SUGGEST_WINDOW = timedelta(days=30)

_REQUIRED_TO_PLAN = ("planned_entry", "planned_stop", "risk_eur", "note_pre")
_TRADE_FIELDS = {c.name for c in Trade.__table__.columns} - {"id", "status"}
_JSON_FIELDS = ("tags", "screenshots")


class StatusError(Exception):
    """Lifecycle guard violation (open a non-planned trade, close a
    non-open one, ...). The API maps this to 409."""


class ManualFill(BaseModel):
    """A fill the user types in because no venue transaction exists for it."""

    ts: datetime
    quantity: Decimal
    price: Decimal
    fee_eur: Decimal = Decimal(0)


def entry_type(trade: Trade) -> TxType:
    return TxType.BUY if trade.direction == Direction.LONG else TxType.SELL


def exit_type(trade: Trade) -> TxType:
    return TxType.SELL if trade.direction == Direction.LONG else TxType.BUY


def next_ref(session: Session) -> str:
    """Next free journal id, `T-001`, `T-002`, ..."""
    highest = 0
    refs = session.execute(
        select(Trade.external_ref).where(Trade.external_ref.like("T-%"))
    ).scalars()
    for ref in refs:
        _, _, digits = ref.partition("-")
        if digits.isdigit():
            highest = max(highest, int(digits))
    return f"T-{highest + 1:03d}"


def require_plan_fields(values: Mapping[str, Any] | Trade) -> None:
    """Entry, stop, risk and a pre-trade note, with a stop that is not the
    entry — a plan you cannot grade later is not a plan.

    Checked when the trade is planned *and* again when it is opened: an edit
    in between can strip a field, and a trade must never reach `open`
    without the numbers its R is measured against.
    """
    get = values.get if isinstance(values, Mapping) else lambda field: getattr(values, field)
    missing = [f for f in _REQUIRED_TO_PLAN if get(f) in (None, "")]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")
    if get("planned_stop") == get("planned_entry"):
        raise ValueError("planned_stop must differ from planned_entry")


def plan_trade(session: Session, data: Mapping[str, Any]) -> Trade:
    """Create a `planned` trade."""
    require_plan_fields(data)

    values = {k: v for k, v in data.items() if k in _TRADE_FIELDS and v is not None}
    for field in _JSON_FIELDS:
        if isinstance(values.get(field), list):
            values[field] = json.dumps(values[field])
    values.setdefault("external_ref", next_ref(session))

    trade = Trade(**values, status=TradeStatus.PLANNED)
    session.add(trade)
    session.commit()
    session.refresh(trade)
    return trade


def open_trade(
    session: Session,
    trade: Trade,
    fill_ids: list[int] | None = None,
    manual: ManualFill | None = None,
) -> Trade:
    """Link the entry fills and move `planned` -> `open`."""
    _require(trade, TradeStatus.PLANNED, "open")
    require_plan_fields(trade)
    fills = _link_fills(session, trade, entry_type(trade), fill_ids, manual)
    trade.status = TradeStatus.OPEN
    trade.opened_ts = min(f.ts for f in fills)
    recompute(trade)
    session.commit()
    session.refresh(trade)
    return trade


def close_trade(
    session: Session,
    trade: Trade,
    fill_ids: list[int] | None = None,
    manual: ManualFill | None = None,
) -> Trade:
    """Link the exit fills and move `open` -> `closed`."""
    _require(trade, TradeStatus.OPEN, "close")
    fills = _link_fills(session, trade, exit_type(trade), fill_ids, manual)
    trade.status = TradeStatus.CLOSED
    trade.closed_ts = max(f.ts for f in fills)
    recompute(trade)
    session.commit()
    session.refresh(trade)
    return trade


def review_trade(
    session: Session,
    trade: Trade,
    adherence: bool | None = None,
    mistake: str | None = None,
    emotion_post: str | None = None,
    note_post: str | None = None,
) -> Trade:
    """Grade a closed trade. Reviewing twice is allowed — it overwrites."""
    _require(trade, TradeStatus.CLOSED, "review")
    trade.adherence = adherence
    trade.mistake = mistake
    trade.emotion_post = emotion_post
    trade.note_post = note_post
    session.commit()
    session.refresh(trade)
    return trade


def cancel_trade(session: Session, trade: Trade) -> Trade:
    """A plan that never triggered. Only from `planned`."""
    _require(trade, TradeStatus.PLANNED, "cancel")
    trade.status = TradeStatus.CANCELLED
    session.commit()
    session.refresh(trade)
    return trade


def recompute(trade: Trade) -> None:
    """Derive the trade's numbers from its linked fills, in place.

    Each fill's EUR value is `amount_eur` (already converted at import time —
    do not apply `fx_rate` again). `avg_entry` is the quantity-weighted mean
    of the entry-side fills, `avg_exit` of the exit-side ones.
    """
    session = object_session(trade)
    fills = (
        session.execute(select(Transaction).where(Transaction.trade_id == trade.id)).scalars().all()
    )
    entries = [f for f in fills if f.type == entry_type(trade)]
    exits = [f for f in fills if f.type == exit_type(trade)]

    trade.fees_eur = sum((f.fee_eur for f in fills), Decimal(0))
    trade.quantity = sum((f.quantity for f in entries), Decimal(0)) or None
    trade.avg_entry = _weighted_avg(entries)
    trade.avg_exit = _weighted_avg(exits)

    if trade.avg_entry is None or trade.avg_exit is None:
        trade.result_eur = None
        return
    sign = Decimal(1) if trade.direction == Direction.LONG else Decimal(-1)
    # ponytail: sized on the entry quantity, so a partial exit reads as if it
    # were full. Weight per closed lot when partial exits actually happen.
    trade.result_eur = (trade.avg_exit - trade.avg_entry) * trade.quantity * sign - trade.fees_eur


def suggest_fills(session: Session, trade: Trade) -> list[Transaction]:
    """Unlinked BUY/SELL transactions on the same account and instrument
    within +/- 30 days of the trade — candidates to link as fills.
    """
    reference = trade.opened_ts or trade.closed_ts or datetime.now(UTC)
    return list(
        session.execute(
            select(Transaction)
            .where(
                Transaction.trade_id.is_(None),
                Transaction.account_id == trade.account_id,
                Transaction.instrument_id == trade.instrument_id,
                Transaction.type.in_((TxType.BUY, TxType.SELL)),
                Transaction.ts >= reference - SUGGEST_WINDOW,
                Transaction.ts <= reference + SUGGEST_WINDOW,
            )
            .order_by(Transaction.ts)
        )
        .scalars()
        .all()
    )


def _require(trade: Trade, status: TradeStatus, action: str) -> None:
    if trade.status != status:
        raise StatusError(f"cannot {action} a {trade.status} trade")


def _weighted_avg(fills: list[Transaction]) -> Decimal | None:
    quantity = sum((f.quantity for f in fills), Decimal(0))
    if not fills or quantity == 0:
        return None
    return sum((f.amount_eur for f in fills), Decimal(0)) / quantity


def _link_fills(
    session: Session,
    trade: Trade,
    side: TxType,
    fill_ids: list[int] | None,
    manual: ManualFill | None,
) -> list[Transaction]:
    fills: list[Transaction] = []
    for fill_id in fill_ids or []:
        tx = session.get(Transaction, fill_id)
        if tx is None:
            raise ValueError(f"transaction {fill_id} not found")
        if tx.trade_id not in (None, trade.id):
            raise ValueError(f"transaction {fill_id} is already linked to trade {tx.trade_id}")
        if tx.account_id != trade.account_id or tx.instrument_id != trade.instrument_id:
            raise ValueError(f"transaction {fill_id} is on another account or instrument")
        if tx.type != side:
            raise ValueError(f"transaction {fill_id} is not a {side} fill")
        tx.trade_id = trade.id
        fills.append(tx)

    if manual is not None:
        fills.append(_manual_transaction(session, trade, side, manual))
    if not fills:
        raise ValueError("at least one fill is required (fill_ids or manual)")
    session.flush()
    return fills


def _manual_transaction(
    session: Session, trade: Trade, side: TxType, manual: ManualFill
) -> Transaction:
    tx = Transaction(
        account_id=trade.account_id,
        ts=manual.ts,
        type=side,
        instrument_id=trade.instrument_id,
        quantity=manual.quantity,
        price=manual.price,
        price_ccy="EUR",
        fee=manual.fee_eur,
        fee_ccy="EUR",
        amount_eur=manual.quantity * manual.price,
        fee_eur=manual.fee_eur,
        external_id=f"manual:{trade.id}:{uuid4().hex[:8]}",
        source=TxSource.MANUAL,
        trade_id=trade.id,
    )
    session.add(tx)
    return tx
