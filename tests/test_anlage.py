"""Form mapping and the CSV exports.

The summary is built by hand here: `anlage.lines` and the exporters only read
it, so they need no database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trade_ledger.enums import FundType, TaxRegime, TxType
from trade_ledger.tax import anlage
from trade_ledger.tax.exports import (
    blockpit_csv,
    cointracking_csv,
    disposals_csv,
    lots_csv,
)
from trade_ledger.tax.fifo import Disposal, FifoResult, Lot
from trade_ledger.tax.forms import FORMS
from trade_ledger.tax.year_summary import (
    InvRow,
    P20Summary,
    P22Summary,
    P23Summary,
    YearSummary,
)

D = Decimal
WHEN = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)


def rows(lines: list[dict], form: str) -> dict[int, str]:
    return {line["zeile"]: line["value"] for line in lines if line["form"] == form}


@pytest.fixture()
def summary() -> YearSummary:
    disposal = Disposal(
        wallet="okx-live",
        instrument_id=1,
        tx_id=7,
        ts=WHEN,
        quantity=D(1),
        proceeds_eur=D(11200),
        cost_eur=D(10000),
        fee_eur=D(0),
        gain_eur=D(1200),
        acquired=datetime(2025, 1, 10, 12, 0, tzinfo=UTC),
        holding_days=142,
        over_one_year=False,
        regime=TaxRegime.P23,
    )
    return YearSummary(
        year=2025,
        p23=P23Summary(
            taxable_gains=D(1200),
            proceeds=D(11200),
            cost=D(10000),
            net=D(1200),
            exceeded=True,
            disposals=[disposal],
        ),
        p22=P22Summary(income=D(300), net=D(300), exceeded=True, items=[object()]),
        p20=P20Summary(
            aktien_gains=D(500),
            aktien_losses=D(50),
            termin_losses=D(30),
            withholding_tax=D(15),
            other_losses=D(80),
            total_foreign=D(620),
        ),
        inv=[
            InvRow(
                instrument=SimpleNamespace(id=1, symbol="VWCE"),
                fund_type=FundType.AKTIEN,
                teilfreistellung_pct=30,
                distributions=D(12),
                vorabpauschale=D("17.71"),
                sale_gain=D(200),
                sale_loss=D(50),
            )
        ],
        disposals=[disposal],
        symbols={1: "BTC"},
    )


def test_anlage_so_carries_the_p23_block(summary):
    so = rows(anlage.lines(summary, 2025), "Anlage SO")

    assert so[45] == "X"
    assert so[46] == "siehe Steuerreport"
    assert so[47] == "01.01.2025 - 31.12.2025"
    assert so[48] == "11200.00"
    assert so[49] == "10000.00"
    assert so[50] == "0.00"
    assert so[51] == "1200.00"
    assert so[15] == "300.00"
    assert so[20] == "300.00"


def test_anlage_kap_lines(summary):
    kap = rows(anlage.lines(summary, 2025), "Anlage KAP")

    assert kap[19] == "620.00"
    assert kap[20] == "500.00"
    assert kap[22] == "80.00"  # termin + fund sale losses, no share losses
    assert kap[23] == "50.00"
    assert kap[41] == "15.00"


def test_anlage_kap_inv_blocks_are_ordered_by_fund_type(summary):
    inv = rows(anlage.lines(summary, 2025), "Anlage KAP-INV")

    assert inv[4] == "12.00"  # distributions, Aktienfonds
    assert inv[5] == "0.00"  # Mischfonds
    assert inv[9] == "17.71"  # Vorabpauschale, Aktienfonds
    assert inv[14] == "200.00"  # sale gains
    assert inv[19] == "50.00"  # sale losses
    assert len(inv) == 20


def test_every_line_is_dated_and_flagged():
    lines = FORMS[2025]
    assert len(lines) == 36
    assert {line.checked for line in lines} == {"2026-09-06"}
    assert {line.note for line in lines} == {"secondary source, VERIFY"}


def test_an_unknown_veranlagungszeitraum_yields_no_lines(summary):
    assert anlage.lines(summary, 2099) == []


def test_empty_blocks_leave_the_checkboxes_empty():
    empty = YearSummary(2025, P23Summary(), P22Summary(), P20Summary())
    so = rows(anlage.lines(empty, 2025), "Anlage SO")
    assert so[45] == ""
    assert so[14] == ""


# --- exports -----------------------------------------------------------------


def tx(**kwargs) -> SimpleNamespace:
    defaults = {
        "id": 1,
        "account_id": 3,
        "instrument_id": 1,
        "ts": WHEN,
        "quantity": D(0),
        "amount_eur": D(0),
        "fee_eur": D(0),
        "external_id": None,
        "note": None,
    }
    return SimpleNamespace(**(defaults | kwargs))


INSTRUMENTS = [SimpleNamespace(id=1, symbol="BTC")]
ACCOUNTS = [SimpleNamespace(id=3, name="okx-live")]


def test_blockpit_buy_row_pays_eur_and_receives_btc():
    buy = tx(type=TxType.BUY, quantity=D("0.5"), amount_eur=D(10000), fee_eur=D(10))

    header, row = blockpit_csv([buy], INSTRUMENTS, ACCOUNTS).splitlines()

    assert header == (
        "Date (UTC),Integration Name,Label,Outgoing Asset,Outgoing Amount,"
        "Incoming Asset,Incoming Amount,Fee Asset,Fee Amount,Trx. ID,Comments"
    )
    assert row == "2025-06-01 12:00:00,okx-live,Trade,EUR,10000,BTC,0.5,EUR,10,1,"


def test_blockpit_sell_row_reverses_the_legs():
    sell = tx(type=TxType.SELL, quantity=D("0.5"), amount_eur=D(11000), external_id="x1")

    row = blockpit_csv([sell], INSTRUMENTS, ACCOUNTS).splitlines()[1]

    assert row == "2025-06-01 12:00:00,okx-live,Trade,BTC,0.5,EUR,11000,,,x1,"


def test_cointracking_columns():
    staking = tx(type=TxType.STAKING_REWARD, quantity=D("0.01"), amount_eur=D(300))

    header, row = cointracking_csv([staking], INSTRUMENTS, ACCOUNTS).splitlines()

    assert header == (
        "Type,Buy Amount,Buy Currency,Sell Amount,Sell Currency,Fee,Fee Currency,"
        "Exchange,Trade-Group,Comment,Date"
    )
    assert row == "Staking,0.01,BTC,,,,,okx-live,,,2025-06-01 12:00:00"


def test_disposals_csv_uses_the_summary_symbols(summary):
    header, row = disposals_csv(summary).splitlines()

    assert header.startswith("wallet,instrument_id,symbol,tx_id,sold,acquired")
    assert row.startswith("okx-live,1,BTC,7,2025-06-01,2025-01-10,142,no,p23")
    assert row.endswith("1,11200,10000,0,1200")


def test_lots_csv_lists_open_lots():
    result = FifoResult(
        open_lots=[
            Lot(
                wallet="okx-live",
                instrument_id=1,
                acquired=WHEN,
                quantity=D(1),
                cost_eur=D(9000),
                source_tx_id=9,
                origin_tx_id=9,
            )
        ]
    )

    header, row = lots_csv(result).splitlines()

    assert header == "wallet,instrument_id,acquired,quantity,cost_eur,source_tx_id,origin_tx_id"
    assert row == "okx-live,1,2025-06-01,1,9000,9,9"
