"""Worked examples for the FIFO engine and the regime table."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_ledger.enums import AccountKind, AssetClass, TaxRegime, TxSource, TxType, Venue
from trade_ledger.models import Transaction
from trade_ledger.tax import InsufficientLots, regime_for, run_fifo

D = Decimal


def ts(year, month, day) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=UTC)


@pytest.fixture()
def tx_factory(session):
    def make(account, instrument, type_, when, **kwargs) -> Transaction:
        row = Transaction(
            account_id=account.id,
            instrument_id=None if instrument is None else instrument.id,
            type=type_,
            ts=when,
            source=TxSource.MANUAL,
            quantity=kwargs.pop("quantity", D(0)),
            amount_eur=kwargs.pop("amount_eur", D(0)),
            fee_eur=kwargs.pop("fee_eur", D(0)),
            **kwargs,
        )
        session.add(row)
        session.flush()
        return row

    return make


@pytest.fixture()
def btc(instrument_factory):
    return instrument_factory(symbol="BTC", asset_class=AssetClass.CRYPTO)


@pytest.fixture()
def okx(account_factory):
    return account_factory(name="okx-spot", kind=AccountKind.CRYPTO_SPOT)


# --- 1: one buy, one partial sell -------------------------------------------


def test_partial_sell_splits_cost_and_keeps_the_rest_open(okx, btc, tx_factory):
    buy = tx_factory(
        okx, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(20000), fee_eur=D(20)
    )
    sell = tx_factory(
        okx, btc, TxType.SELL, ts(2025, 6, 1), quantity=D("0.4"), amount_eur=D(12000), fee_eur=D(12)
    )

    result = run_fifo([buy, sell], [btc], [okx])

    assert result.warnings == []
    (disposal,) = result.disposals
    assert disposal.wallet == "okx-spot"
    assert disposal.instrument_id == btc.id
    assert disposal.tx_id == sell.id
    assert disposal.ts == ts(2025, 6, 1)
    assert disposal.quantity == D("0.4")
    assert disposal.proceeds_eur == D(12000)
    assert disposal.cost_eur == D("8008")
    assert disposal.fee_eur == D(12)
    assert disposal.gain_eur == D(3980)
    assert disposal.acquired == ts(2025, 1, 10)
    assert disposal.holding_days == 142
    assert disposal.over_one_year is False
    assert disposal.regime == TaxRegime.P23

    (lot,) = result.open_lots
    assert lot.quantity == D("0.6")
    assert lot.cost_eur == D("12012")
    assert lot.acquired == ts(2025, 1, 10)
    assert lot.source_tx_id == buy.id
    assert lot.origin_tx_id == buy.id


# --- 2: the twelve-month line -----------------------------------------------


@pytest.mark.parametrize(
    ("sold_on", "over_one_year", "holding_days"),
    [(ts(2026, 1, 10), False, 365), (ts(2026, 1, 11), True, 366)],
)
def test_one_year_rule_is_strictly_greater(
    okx, btc, tx_factory, sold_on, over_one_year, holding_days
):
    buy = tx_factory(
        okx, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(20000), fee_eur=D(20)
    )
    sell = tx_factory(
        okx, btc, TxType.SELL, sold_on, quantity=D("0.4"), amount_eur=D(12000), fee_eur=D(12)
    )

    (disposal,) = run_fifo([buy, sell], [btc], [okx]).disposals

    assert disposal.over_one_year is over_one_year
    assert disposal.holding_days == holding_days
    assert disposal.gain_eur == D(3980)


# --- 3: a sell spanning two lots --------------------------------------------


def test_sell_across_two_lots_yields_two_disposals(okx, btc, tx_factory):
    first = tx_factory(
        okx, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(20000), fee_eur=D(20)
    )
    second = tx_factory(
        okx, btc, TxType.BUY, ts(2025, 3, 10), quantity=D(1), amount_eur=D(30000), fee_eur=D(30)
    )
    sell = tx_factory(
        okx, btc, TxType.SELL, ts(2025, 6, 1), quantity=D("1.5"), amount_eur=D(45000), fee_eur=D(45)
    )

    result = run_fifo([first, second, sell], [btc], [okx])

    older, newer = result.disposals
    assert older.quantity == D(1)
    assert older.acquired == ts(2025, 1, 10)
    assert older.cost_eur == D(20020)
    assert older.proceeds_eur == D(30000)
    assert older.fee_eur == D(30)
    assert older.gain_eur == D(9950)
    assert older.holding_days == 142

    assert newer.quantity == D("0.5")
    assert newer.acquired == ts(2025, 3, 10)
    assert newer.cost_eur == D(15015)
    assert newer.proceeds_eur == D(15000)
    assert newer.fee_eur == D(15)
    assert newer.gain_eur == D(-30)
    assert newer.holding_days == 83

    # nothing lost in the split
    assert older.proceeds_eur + newer.proceeds_eur == D(45000)
    assert older.fee_eur + newer.fee_eur == D(45)

    (lot,) = result.open_lots
    assert lot.quantity == D("0.5")
    assert lot.cost_eur == D(15015)


# --- 4: a linked transfer carries the acquisition date ----------------------


def test_linked_transfer_keeps_acquired_date_and_cost(account_factory, okx, btc, tx_factory):
    wallet = account_factory(name="ledger-nano", venue=Venue.WALLET, kind=AccountKind.WALLET)
    buy = tx_factory(
        okx, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(20000), fee_eur=D(20)
    )
    out = tx_factory(okx, btc, TxType.TRANSFER_OUT, ts(2025, 2, 1), quantity=D(1), link_id="mv-1")
    into = tx_factory(
        wallet, btc, TxType.TRANSFER_IN, ts(2025, 2, 1), quantity=D(1), link_id="mv-1"
    )
    sell = tx_factory(
        wallet, btc, TxType.SELL, ts(2026, 3, 1), quantity=D(1), amount_eur=D(30000), fee_eur=D(30)
    )

    result = run_fifo([sell, into, out, buy], [btc], [okx, wallet])

    assert result.warnings == []
    assert result.open_lots == []
    (disposal,) = result.disposals
    assert disposal.wallet == "ledger-nano"
    assert disposal.acquired == ts(2025, 1, 10)  # not the transfer date
    assert disposal.cost_eur == D(20020)
    assert disposal.proceeds_eur == D(30000)
    assert disposal.fee_eur == D(30)
    assert disposal.gain_eur == D(9950)
    assert disposal.holding_days == 415
    assert disposal.over_one_year is True
    assert disposal.regime == TaxRegime.P23


def test_transfer_out_alone_leaves_lots_in_transit(okx, btc, tx_factory):
    buy = tx_factory(
        okx, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(20000), fee_eur=D(20)
    )
    out = tx_factory(okx, btc, TxType.TRANSFER_OUT, ts(2025, 2, 1), quantity=D(1), link_id="mv-1")

    result = run_fifo([buy, out], [btc], [okx])

    assert result.warnings == ["transfer mv-1 never arrived, 1 lot(s) in transit"]
    (lot,) = result.open_lots
    assert lot.acquired == ts(2025, 1, 10)
    assert lot.cost_eur == D(20020)


def test_unlinked_transfer_out_drops_the_lots(okx, btc, tx_factory):
    buy = tx_factory(
        okx, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(20000), fee_eur=D(20)
    )
    out = tx_factory(okx, btc, TxType.TRANSFER_OUT, ts(2025, 2, 1), quantity=D(1))

    result = run_fifo([buy, out], [btc], [okx])

    assert result.warnings == [f"tx {out.id}: unlinked transfer_out, lots dropped"]
    assert result.open_lots == []
    assert result.disposals == []


# --- 5: an unlinked transfer in ---------------------------------------------


def test_unlinked_transfer_in_has_no_cost_basis(okx, btc, tx_factory):
    into = tx_factory(okx, btc, TxType.TRANSFER_IN, ts(2025, 2, 1), quantity=D(2))

    result = run_fifo([into], [btc], [okx])

    assert result.warnings == [f"tx {into.id}: unlinked transfer_in, cost basis unknown"]
    (lot,) = result.open_lots
    assert lot.quantity == D(2)
    assert lot.cost_eur == D(0)
    assert lot.acquired == ts(2025, 2, 1)
    assert lot.origin_tx_id == into.id


def test_pending_fx_row_is_flagged_and_valued_at_zero(okx, btc, tx_factory):
    buy = tx_factory(
        okx, btc, TxType.BUY, ts(2025, 2, 1), quantity=D(1), amount_eur=D(0), fx_source="pending"
    )

    result = run_fifo([buy], [btc], [okx])

    assert result.warnings == [f"tx {buy.id} has no EUR value (fx pending)"]
    (lot,) = result.open_lots
    assert lot.cost_eur == D(0)


# --- 6: selling more than the wallet holds ----------------------------------


def test_oversold_wallet_raises_in_strict_mode(okx, btc, tx_factory):
    buy = tx_factory(
        okx, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(20000), fee_eur=D(20)
    )
    sell = tx_factory(
        okx, btc, TxType.SELL, ts(2025, 6, 1), quantity=D("1.5"), amount_eur=D(45000), fee_eur=D(45)
    )

    with pytest.raises(InsufficientLots) as excinfo:
        run_fifo([buy, sell], [btc], [okx], strict=True)

    assert excinfo.value.wallet == "okx-spot"
    assert excinfo.value.instrument_id == btc.id
    assert excinfo.value.ts == ts(2025, 6, 1)
    assert excinfo.value.missing == D("0.5")


def test_oversold_wallet_warns_and_assumes_zero_cost(okx, btc, tx_factory):
    buy = tx_factory(
        okx, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(20000), fee_eur=D(20)
    )
    sell = tx_factory(
        okx, btc, TxType.SELL, ts(2025, 6, 1), quantity=D("1.5"), amount_eur=D(45000), fee_eur=D(45)
    )

    result = run_fifo([buy, sell], [btc], [okx])

    assert result.warnings == [
        f"tx {sell.id}: only partial lots for 0.5 of okx-spot/{btc.id}, zero-cost lot assumed"
    ]
    real, synthetic = result.disposals
    assert real.quantity == D(1)
    assert real.cost_eur == D(20020)
    assert real.proceeds_eur == D(30000)
    assert synthetic.quantity == D("0.5")
    assert synthetic.cost_eur == D(0)
    assert synthetic.proceeds_eur == D(15000)
    assert synthetic.fee_eur == D(15)
    assert synthetic.gain_eur == D(14985)
    assert synthetic.acquired == ts(2025, 6, 1)
    assert synthetic.holding_days == 0
    assert synthetic.over_one_year is False
    assert result.open_lots == []


# --- 7: a share split -------------------------------------------------------


def test_split_doubles_quantity_and_keeps_cost_and_date(
    account_factory, instrument_factory, tx_factory
):
    broker = account_factory(name="t212", venue=Venue.TRADING212, kind=AccountKind.BROKER_INVEST)
    stock = instrument_factory(symbol="AAPL", asset_class=AssetClass.STOCK)
    buy = tx_factory(
        broker, stock, TxType.BUY, ts(2025, 1, 10), quantity=D(10), amount_eur=D(2000), fee_eur=D(1)
    )
    split = tx_factory(broker, stock, TxType.SPLIT, ts(2025, 5, 1), quantity=D(10))
    sell = tx_factory(
        broker, stock, TxType.SELL, ts(2025, 6, 1), quantity=D(5), amount_eur=D(600), fee_eur=D(1)
    )

    result = run_fifo([buy, split, sell], [stock], [broker])

    assert result.warnings == []
    (disposal,) = result.disposals
    assert disposal.quantity == D(5)
    assert disposal.cost_eur == D("500.25")  # a quarter of 2.001
    assert disposal.proceeds_eur == D(600)
    assert disposal.fee_eur == D(1)
    assert disposal.gain_eur == D("98.75")
    assert disposal.acquired == ts(2025, 1, 10)  # the split does not restart the clock
    assert disposal.regime == TaxRegime.P20_AKTIEN

    (lot,) = result.open_lots
    assert lot.quantity == D(15)
    assert lot.cost_eur == D("1500.75")
    assert lot.acquired == ts(2025, 1, 10)
    assert lot.origin_tx_id == buy.id


def test_split_across_two_lots_loses_no_dust(okx, btc, tx_factory):
    first = tx_factory(okx, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(20000))
    second = tx_factory(okx, btc, TxType.BUY, ts(2025, 2, 10), quantity=D(2), amount_eur=D(50000))
    split = tx_factory(okx, btc, TxType.SPLIT, ts(2025, 5, 1), quantity=D(1))

    result = run_fifo([first, second, split], [btc], [okx])

    older, newer = result.open_lots
    assert older.quantity + newer.quantity == D(4)
    assert older.cost_eur == D(20000)
    assert newer.cost_eur == D(50000)


# --- 8: the regime table ----------------------------------------------------


@pytest.mark.parametrize(
    ("tx_type", "asset_class", "kind", "amount", "expected"),
    [
        (TxType.SELL, AssetClass.CRYPTO, AccountKind.CRYPTO_SPOT, D(1), TaxRegime.P23),
        (TxType.SELL, AssetClass.CRYPTO, AccountKind.WALLET, D(1), TaxRegime.P23),
        (
            TxType.SELL,
            AssetClass.CRYPTO,
            AccountKind.CRYPTO_DERIVATIVES,
            D(1),
            TaxRegime.P20_TERMIN,
        ),
        (TxType.SELL, AssetClass.PERP, AccountKind.CRYPTO_DERIVATIVES, D(1), TaxRegime.P20_TERMIN),
        (TxType.SELL, AssetClass.CFD, AccountKind.BROKER_CFD, D(1), TaxRegime.P20_TERMIN),
        (TxType.SELL, AssetClass.STOCK, AccountKind.BROKER_INVEST, D(1), TaxRegime.P20_AKTIEN),
        (TxType.SELL, AssetClass.ETF, AccountKind.BROKER_INVEST, D(1), TaxRegime.P20_INV),
        (TxType.SELL, AssetClass.FX, AccountKind.BROKER_INVEST, D(1), TaxRegime.P23),
        (
            TxType.DIVIDEND,
            AssetClass.STOCK,
            AccountKind.BROKER_INVEST,
            D(5),
            TaxRegime.P20_SONSTIGE,
        ),
        (
            TxType.INTEREST,
            AssetClass.STOCK,
            AccountKind.BROKER_INVEST,
            D(5),
            TaxRegime.P20_SONSTIGE,
        ),
        (TxType.STAKING_REWARD, AssetClass.CRYPTO, AccountKind.CRYPTO_SPOT, D(5), TaxRegime.P22),
        (TxType.AIRDROP, AssetClass.CRYPTO, AccountKind.WALLET, D(5), TaxRegime.P22),
        (TxType.AIRDROP, AssetClass.CRYPTO, AccountKind.WALLET, D(0), TaxRegime.NONE),
        (TxType.DEPOSIT, None, AccountKind.BROKER_INVEST, D(100), TaxRegime.NONE),
    ],
)
def test_regime_table(
    account_factory, instrument_factory, tx_factory, tx_type, asset_class, kind, amount, expected
):
    account = account_factory(kind=kind)
    instrument = None if asset_class is None else instrument_factory(asset_class=asset_class)
    tx = tx_factory(account, instrument, tx_type, ts(2025, 6, 1), quantity=D(1), amount_eur=amount)

    assert regime_for(tx, instrument, account) == expected


def test_instrument_regime_override_wins(okx, instrument_factory, tx_factory):
    instrument = instrument_factory(
        symbol="MSTR", asset_class=AssetClass.CRYPTO, tax_regime=TaxRegime.P20_AKTIEN
    )
    tx = tx_factory(okx, instrument, TxType.SELL, ts(2025, 6, 1), quantity=D(1), amount_eur=D(100))

    assert regime_for(tx, instrument, okx) == TaxRegime.P20_AKTIEN


def test_carried_lot_keeps_its_place_in_the_fifo_queue(account_factory, okx, btc, tx_factory):
    """A lot moved in from elsewhere is consumed by age, not by arrival order."""
    wallet = account_factory(name="cold", venue=Venue.WALLET, kind=AccountKind.WALLET)
    old_buy = tx_factory(okx, btc, TxType.BUY, ts(2025, 1, 10), quantity=D(1), amount_eur=D(20000))
    new_buy = tx_factory(
        wallet, btc, TxType.BUY, ts(2025, 2, 10), quantity=D(1), amount_eur=D(30000)
    )
    out = tx_factory(okx, btc, TxType.TRANSFER_OUT, ts(2025, 3, 1), quantity=D(1), link_id="mv-2")
    into = tx_factory(
        wallet, btc, TxType.TRANSFER_IN, ts(2025, 3, 1), quantity=D(1), link_id="mv-2"
    )
    sell = tx_factory(wallet, btc, TxType.SELL, ts(2025, 4, 1), quantity=D(1), amount_eur=D(40000))

    result = run_fifo([old_buy, new_buy, out, into, sell], [btc], [okx, wallet])

    (disposal,) = result.disposals
    assert disposal.acquired == ts(2025, 1, 10)  # the older, transferred-in lot goes first
    assert disposal.cost_eur == D(20000)
    assert disposal.gain_eur == D(20000)
    (lot,) = result.open_lots
    assert lot.acquired == ts(2025, 2, 10)
    assert lot.cost_eur == D(30000)
