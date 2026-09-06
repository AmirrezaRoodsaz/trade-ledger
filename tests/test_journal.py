from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_ledger.enums import Direction, TradeStatus, TxSource, TxType
from trade_ledger.journal import (
    ManualFill,
    StatusError,
    close_trade,
    next_ref,
    open_trade,
    plan_trade,
    review_trade,
    suggest_fills,
)
from trade_ledger.models import Transaction

TS = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)


def _plan(session, account, instrument, direction=Direction.LONG, **overrides):
    data = {
        "account_id": account.id,
        "instrument_id": instrument.id,
        "direction": direction,
        "planned_entry": Decimal(100),
        "planned_stop": Decimal(95),
        "risk_eur": Decimal(50),
        "note_pre": "breakout over the range high",
    }
    data.update(overrides)
    return plan_trade(session, data)


def test_plan_without_stop_is_rejected(session, account_factory, instrument_factory):
    with pytest.raises(ValueError, match="planned_stop"):
        _plan(session, account_factory(), instrument_factory(), planned_stop=None)


def test_plan_with_stop_equal_to_entry_is_rejected(session, account_factory, instrument_factory):
    with pytest.raises(ValueError, match="must differ"):
        _plan(session, account_factory(), instrument_factory(), planned_stop=Decimal(100))


def test_next_ref_increments(session, account_factory, instrument_factory):
    account, instrument = account_factory(), instrument_factory()
    assert next_ref(session) == "T-001"
    assert _plan(session, account, instrument).external_ref == "T-001"
    assert _plan(session, account, instrument).external_ref == "T-002"
    assert next_ref(session) == "T-003"


def test_long_trade_result_and_r(session, account_factory, instrument_factory):
    trade = _plan(session, account_factory(), instrument_factory())

    open_trade(
        session,
        trade,
        manual=ManualFill(ts=TS, quantity=Decimal(10), price=Decimal(100), fee_eur=Decimal(1)),
    )
    assert trade.status == TradeStatus.OPEN
    assert trade.avg_entry == Decimal(100)
    assert trade.opened_ts == TS
    assert trade.result_eur is None

    close_trade(
        session,
        trade,
        manual=ManualFill(
            ts=TS + timedelta(days=1),
            quantity=Decimal(10),
            price=Decimal(120),
            fee_eur=Decimal(1),
        ),
    )
    assert trade.status == TradeStatus.CLOSED
    assert trade.avg_exit == Decimal(120)
    assert trade.quantity == Decimal(10)
    assert trade.fees_eur == Decimal(2)
    assert trade.result_eur == Decimal(198)
    assert trade.r_multiple == Decimal("3.96")


def test_short_trade_result_is_mirrored(session, account_factory, instrument_factory):
    trade = _plan(
        session,
        account_factory(),
        instrument_factory(),
        direction=Direction.SHORT,
        planned_stop=Decimal(105),
    )
    open_trade(
        session,
        trade,
        manual=ManualFill(ts=TS, quantity=Decimal(10), price=Decimal(100), fee_eur=Decimal(1)),
    )
    close_trade(
        session,
        trade,
        manual=ManualFill(
            ts=TS + timedelta(days=1),
            quantity=Decimal(10),
            price=Decimal(80),
            fee_eur=Decimal(1),
        ),
    )
    assert trade.avg_entry == Decimal(100)
    assert trade.avg_exit == Decimal(80)
    assert trade.result_eur == Decimal(198)
    assert trade.r_multiple == Decimal("3.96")


def test_manual_fill_rows_are_linked_and_typed(session, account_factory, instrument_factory):
    trade = _plan(session, account_factory(), instrument_factory(), direction=Direction.SHORT)
    open_trade(session, trade, manual=ManualFill(ts=TS, quantity=Decimal(2), price=Decimal(50)))
    fill = session.query(Transaction).filter(Transaction.trade_id == trade.id).one()
    assert fill.type == TxType.SELL  # short entry
    assert fill.source == TxSource.MANUAL
    assert fill.amount_eur == Decimal(100)
    assert fill.external_id.startswith(f"manual:{trade.id}:")


def test_status_guards(session, account_factory, instrument_factory):
    account, instrument = account_factory(), instrument_factory()
    trade = _plan(session, account, instrument)

    with pytest.raises(StatusError):
        close_trade(session, trade, manual=_fill())
    with pytest.raises(StatusError):
        review_trade(session, trade, adherence=True)

    open_trade(session, trade, manual=_fill())
    with pytest.raises(StatusError, match="cannot open an? open trade"):
        open_trade(session, trade, manual=_fill())

    close_trade(session, trade, manual=_fill(price=Decimal(110)))
    review_trade(session, trade, adherence=False, mistake="early_exit", note_post="cut too soon")
    assert trade.adherence is False
    assert trade.mistake == "early_exit"


def test_open_rechecks_the_planned_fields(session, account_factory, instrument_factory):
    trade = _plan(session, account_factory(), instrument_factory())
    trade.risk_eur = None  # an edit between plan and open
    with pytest.raises(ValueError, match="risk_eur"):
        open_trade(session, trade, manual=_fill())
    assert trade.status == TradeStatus.PLANNED


def test_open_requires_a_fill(session, account_factory, instrument_factory):
    trade = _plan(session, account_factory(), instrument_factory())
    with pytest.raises(ValueError, match="at least one fill"):
        open_trade(session, trade)


def test_suggest_fills_only_unlinked_same_instrument(session, account_factory, instrument_factory):
    account = account_factory()
    other_account = account_factory()
    instrument = instrument_factory()
    other_instrument = instrument_factory()
    trade = _plan(session, account, instrument)
    open_trade(session, trade, manual=_fill())  # linked, must not be suggested

    candidate = _tx(session, account, instrument, TS)
    _tx(session, account, other_instrument, TS)  # wrong instrument
    _tx(session, other_account, instrument, TS)  # wrong account
    _tx(session, account, instrument, TS + timedelta(days=90))  # outside the window
    _tx(session, account, instrument, TS, type=TxType.DEPOSIT)  # not a fill type
    session.flush()

    assert [tx.id for tx in suggest_fills(session, trade)] == [candidate.id]


def _fill(price: Decimal = Decimal(100)) -> ManualFill:
    return ManualFill(ts=TS, quantity=Decimal(1), price=price)


def _tx(session, account, instrument, ts, type=TxType.BUY):
    tx = Transaction(
        account_id=account.id,
        ts=ts,
        type=type,
        instrument_id=instrument.id,
        quantity=Decimal(1),
        amount_eur=Decimal(100),
        source=TxSource.MANUAL,
    )
    session.add(tx)
    session.flush()
    return tx
