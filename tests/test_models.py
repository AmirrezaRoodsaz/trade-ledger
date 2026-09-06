from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from trade_ledger import settings
from trade_ledger.enums import Direction, TradeStatus, TxSource, TxType
from trade_ledger.models import Setting, Trade, Transaction


def test_money_roundtrip_keeps_full_precision(session, account_factory, instrument_factory):
    account = account_factory()
    instrument = instrument_factory()
    tx = Transaction(
        account_id=account.id,
        ts=datetime.now(UTC),
        type=TxType.BUY,
        instrument_id=instrument.id,
        quantity=Decimal("0.00000001"),
        source=TxSource.MANUAL,
    )
    session.add(tx)
    session.commit()
    session.expire_all()

    reloaded = session.get(Transaction, tx.id)
    assert reloaded.quantity == Decimal("0.00000001")
    assert isinstance(reloaded.quantity, Decimal)


def test_utc_datetime_roundtrips_aware_datetime(session, account_factory):
    account = account_factory()
    tz_plus5 = timezone(timedelta(hours=5))
    ts = datetime(2026, 3, 1, 12, 0, 0, tzinfo=tz_plus5)
    tx = Transaction(
        account_id=account.id,
        ts=ts,
        type=TxType.DEPOSIT,
        source=TxSource.MANUAL,
    )
    session.add(tx)
    session.commit()
    session.expire_all()

    reloaded = session.get(Transaction, tx.id)
    assert reloaded.ts.tzinfo is not None
    assert reloaded.ts == ts  # same instant
    assert reloaded.ts.utcoffset() == timedelta(0)  # normalised to UTC on write


def test_unique_account_external_id_raises_integrity_error(session, account_factory):
    account = account_factory()
    now = datetime.now(UTC)
    session.add(
        Transaction(
            account_id=account.id,
            ts=now,
            type=TxType.DEPOSIT,
            source=TxSource.MANUAL,
            external_id="dup-1",
        )
    )
    session.flush()
    session.add(
        Transaction(
            account_id=account.id,
            ts=now,
            type=TxType.DEPOSIT,
            source=TxSource.MANUAL,
            external_id="dup-1",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_trade_r_multiple(session, account_factory, instrument_factory):
    account = account_factory()
    instrument = instrument_factory()
    trade = Trade(
        account_id=account.id,
        instrument_id=instrument.id,
        direction=Direction.LONG,
        status=TradeStatus.CLOSED,
        result_eur=Decimal(50),
        risk_eur=Decimal(25),
    )
    assert trade.r_multiple == Decimal(2)

    trade_no_risk = Trade(
        account_id=account.id,
        instrument_id=instrument.id,
        direction=Direction.LONG,
        status=TradeStatus.CLOSED,
        result_eur=Decimal(50),
        risk_eur=None,
    )
    assert trade_no_risk.r_multiple is None


def test_account_tax_wallet_defaults_to_name(account_factory):
    account = account_factory(name="okx-main", tax_wallet=None)
    assert account.tax_wallet == "okx-main"


def test_init_db_seeds_schema_version(session):
    row = session.get(Setting, "schema_version")
    assert row is not None
    assert row.value == "1"


def test_env_status_reports_booleans_only():
    status = settings.env_status()
    assert status  # .env.example declares at least one key
    assert all(isinstance(v, bool) for v in status.values())
    assert "DB_PATH" in status
