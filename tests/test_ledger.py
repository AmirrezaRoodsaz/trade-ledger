from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from trade_ledger.enums import AssetClass, TxSource, TxType
from trade_ledger.ledger import (
    TxDraft,
    cash_balance,
    cash_delta_eur,
    get_or_create_instrument,
    position_delta,
    positions,
    upsert_transactions,
)
from trade_ledger.models import Transaction


def _tx(**kwargs) -> Transaction:
    defaults = {"type": TxType.BUY, "instrument_id": 1}
    defaults.update(kwargs)
    return Transaction(**defaults)


def test_buy_cash_and_position_delta():
    tx = _tx(
        type=TxType.BUY,
        quantity=Decimal("0.5"),
        amount_eur=Decimal(20000),
        fee_eur=Decimal(12),
    )
    assert cash_delta_eur(tx) == Decimal(-20012)
    assert position_delta(tx) == Decimal("0.5")


def test_sell_cash_and_position_delta():
    tx = _tx(
        type=TxType.SELL,
        quantity=Decimal("0.5"),
        amount_eur=Decimal(20000),
        fee_eur=Decimal(12),
    )
    assert cash_delta_eur(tx) == Decimal(19988)
    assert position_delta(tx) == Decimal("-0.5")


def test_dividend_nets_withholding_tax():
    tx = _tx(
        type=TxType.DIVIDEND,
        instrument_id=1,
        amount_eur=Decimal(100),
        fee_eur=Decimal(0),
        withholding_tax_eur=Decimal(15),
    )
    assert cash_delta_eur(tx) == Decimal(85)
    assert position_delta(tx) == Decimal(0)  # dividend in cash never moves the position


def test_hash_external_id_is_stable_and_dedupes_second_import(session, account_factory):
    account = account_factory()
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    draft = TxDraft(
        ts=ts,
        type=TxType.BUY,
        quantity=Decimal("0.5"),
        amount_eur=Decimal(20000),
        fee_eur=Decimal(12),
        source=TxSource.CSV,
        instrument_symbol="BTC",
        asset_class=AssetClass.CRYPTO,
    )

    added, skipped = upsert_transactions(session, account, [draft])
    assert (added, skipped) == (1, 0)

    # A second import of the same row (no external_id given) hashes to the
    # same id and is skipped rather than duplicated.
    added2, skipped2 = upsert_transactions(session, account, [draft])
    assert (added2, skipped2) == (0, 1)

    tx_count = len(session.execute(select(Transaction)).scalars().all())
    assert tx_count == 1


def test_positions_after_buy_then_partial_sell(session, account_factory, instrument_factory):
    account = account_factory()
    instrument = instrument_factory(symbol="BTC", asset_class=AssetClass.CRYPTO)
    drafts = [
        TxDraft(
            ts=datetime(2026, 1, 1, tzinfo=UTC),
            type=TxType.BUY,
            quantity=Decimal(1),
            amount_eur=Decimal(40000),
            source=TxSource.MANUAL,
            instrument_symbol="BTC",
            asset_class=AssetClass.CRYPTO,
        ),
        TxDraft(
            ts=datetime(2026, 1, 2, tzinfo=UTC),
            type=TxType.SELL,
            quantity=Decimal("0.4"),
            amount_eur=Decimal(18000),
            source=TxSource.MANUAL,
            instrument_symbol="BTC",
            asset_class=AssetClass.CRYPTO,
        ),
    ]
    added, skipped = upsert_transactions(session, account, drafts)
    assert (added, skipped) == (2, 0)

    pos = positions(session, [account.id])
    assert pos == {instrument.id: Decimal("0.6")}


def test_get_or_create_instrument_matches_on_symbol_and_asset_class(session):
    first = get_or_create_instrument(session, "BTC", AssetClass.CRYPTO)
    again = get_or_create_instrument(session, "BTC", AssetClass.CRYPTO, name="Bitcoin")
    assert first.id == again.id


def test_cash_balance_sums_deltas(session, account_factory):
    account = account_factory()
    drafts = [
        TxDraft(
            ts=datetime(2026, 1, 1, tzinfo=UTC),
            type=TxType.DEPOSIT,
            amount_eur=Decimal(1000),
            source=TxSource.MANUAL,
        ),
        TxDraft(
            ts=datetime(2026, 1, 2, tzinfo=UTC),
            type=TxType.WITHDRAWAL,
            amount_eur=Decimal(200),
            fee_eur=Decimal(1),
            source=TxSource.MANUAL,
        ),
    ]
    upsert_transactions(session, account, drafts)
    assert cash_balance(session, account.id) == Decimal(799)
