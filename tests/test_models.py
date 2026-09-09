from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from trade_ledger import db, settings
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
    assert row.value == db.SCHEMA_VERSION


def test_env_status_detects_presence(tmp_path, monkeypatch):
    example = tmp_path / ".env.example"
    example.write_text("SET_KEY=\nABSENT_KEY=\n")
    env_file = tmp_path / ".env"
    env_file.write_text("SET_KEY=abc123\n")
    monkeypatch.setattr(settings, "_ENV_EXAMPLE", example)
    monkeypatch.setattr(settings, "_ENV_FILE", env_file)

    status = settings.env_status()

    assert status == {"SET_KEY": True, "ABSENT_KEY": False}


def test_relative_db_and_data_paths_resolve_against_the_repo_root(monkeypatch):
    """Started from anywhere, the app must open the same database and write
    screenshots to the same directory — cwd-relative defaults did not.
    """
    monkeypatch.setenv("DB_PATH", "data/ledger.db")
    monkeypatch.setenv("DATA_DIR", "data")
    resolved = settings.Settings()

    assert resolved.DB_PATH == str(settings._REPO_ROOT / "data" / "ledger.db")
    assert resolved.DATA_DIR == str(settings._REPO_ROOT / "data")

    monkeypatch.setenv("DB_PATH", ":memory:")  # SQLite's marker, not a path
    assert settings.Settings().DB_PATH == ":memory:"

    monkeypatch.setenv("DB_PATH", "/tmp/other.db")
    assert settings.Settings().DB_PATH == "/tmp/other.db"


def test_one_journal_ref_per_bot(session, account_factory, instrument_factory, bot_factory):
    """The runner derives its ref from bot, symbol and bar, so a re-run of the
    same bar must find the plan it filed rather than file a second one. The
    index is what makes that a guarantee.
    """
    account, instrument = account_factory(), instrument_factory()
    bot, _ = bot_factory()
    fields = {
        "account_id": account.id,
        "instrument_id": instrument.id,
        "direction": Direction.LONG,
        "external_ref": "bot0000000001",
    }
    session.add(Trade(**fields, bot_id=bot.id))
    session.commit()

    session.add(Trade(**fields, bot_id=bot.id))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # A hand-entered trade has no bot, and SQLite counts NULLs as distinct —
    # the manual journal is untouched by the index.
    session.add(Trade(**{**fields, "external_ref": "T-001"}))
    session.add(Trade(**{**fields, "external_ref": "T-001"}))
    session.commit()


def test_a_database_with_a_duplicate_journal_ref_still_opens(tmp_path, caplog):
    """The index is a guarantee for new data, never a reason to refuse an
    existing file. SQLite rejects the index with `IntegrityError` when rows
    already collide — `init_db` warns and carries on without it.
    """
    path = tmp_path / "ledger.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL,"
            " instrument_id INTEGER NOT NULL, direction VARCHAR NOT NULL, status VARCHAR,"
            " external_ref VARCHAR, bot_id INTEGER)"
        )
        conn.executemany(
            "INSERT INTO trades (account_id, instrument_id, direction, external_ref, bot_id)"
            " VALUES (1, 1, 'long', 'ref1', 7)",
            [(), ()],
        )

    with caplog.at_level(logging.WARNING, logger="trade_ledger.db"):
        db.init_db(path)

    with db.engine.begin() as conn:
        indexes = {row[1] for row in conn.exec_driver_sql("PRAGMA index_list(trades)")}
    assert "uq_trades_bot_ref" not in indexes
    assert "uq_trades_bot_ref" in caplog.text
