"""CSV exports: the ledger's own disposal and lot lists, plus the import
formats of the two tax tools a German private investor is most likely to feed
this into (Blockpit and CoinTracking).

Column names and their order are the tools' own, checked 2026-09-06 against
secondary sources — VERIFY against the tool's current template before trusting
an import. Everything is UTF-8, comma separated, dot decimal separator. Fees go
out in EUR (`fee_eur`) rather than in the native fee currency; both tools take
a EUR fee, and `fee_eur` is the figure the rest of the ledger reconciles to.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from ..enums import TxType

# ponytail: a best-effort map onto Blockpit's `Label` vocabulary, guessed from
# secondary documentation. Ceiling: an unknown type lands on "Other" and needs
# fixing by hand in the tool. Check the current template before a real import.
_BLOCKPIT_LABEL = {
    TxType.BUY: "Trade",
    TxType.SELL: "Trade",
    TxType.DEPOSIT: "Deposit",
    TxType.WITHDRAWAL: "Withdrawal",
    TxType.TRANSFER_IN: "Deposit",
    TxType.TRANSFER_OUT: "Withdrawal",
    TxType.DIVIDEND: "Dividend",
    TxType.INTEREST: "Interest",
    TxType.STAKING_REWARD: "Staking",
    TxType.AIRDROP: "Airdrop",
    TxType.FEE: "Fee",
}
_COINTRACKING_TYPE = dict(_BLOCKPIT_LABEL) | {
    TxType.STAKING_REWARD: "Staking",
    TxType.AIRDROP: "Airdrop",
    TxType.FEE: "Other Fee",
}


def _csv(header: list[str], rows) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def _num(value) -> str:
    return "" if value is None else f"{Decimal(value):f}"


def _legs(tx, symbol: str) -> tuple[str, str, str, str]:
    """`(incoming asset, incoming amount, outgoing asset, outgoing amount)`.

    The instrument leg is quantity in the instrument's own symbol; the cash leg
    is `amount_eur` in EUR. A transfer or an in-kind reward has one leg only.
    """
    quantity, cash = _num(tx.quantity), _num(tx.amount_eur)
    if tx.type == TxType.BUY:
        return symbol, quantity, "EUR", cash
    if tx.type == TxType.SELL:
        return "EUR", cash, symbol, quantity
    if tx.type in (TxType.DEPOSIT, TxType.INTEREST, TxType.DIVIDEND):
        return "EUR", cash, "", ""
    if tx.type in (TxType.WITHDRAWAL, TxType.FEE):
        return "", "", "EUR", cash
    if tx.type in (TxType.TRANSFER_IN, TxType.STAKING_REWARD, TxType.AIRDROP):
        return symbol, quantity, "", ""
    if tx.type == TxType.TRANSFER_OUT:
        return "", "", symbol, quantity
    return "", "", "", ""


def _lookup(instruments, accounts):
    symbols = {i.id: i.symbol for i in instruments}
    names = {a.id: a.name for a in accounts}
    return symbols, names


def disposals_csv(summary) -> str:
    """Every disposal of the tax year, all regimes, one row per lot matched."""
    symbols = summary.symbols
    header = [
        "wallet",
        "instrument_id",
        "symbol",
        "tx_id",
        "sold",
        "acquired",
        "holding_days",
        "over_one_year",
        "regime",
        "quantity",
        "proceeds_eur",
        "cost_eur",
        "fee_eur",
        "gain_eur",
    ]
    rows = [
        [
            d.wallet,
            d.instrument_id,
            symbols.get(d.instrument_id, ""),
            d.tx_id,
            d.ts.date().isoformat(),
            d.acquired.date().isoformat(),
            d.holding_days,
            "yes" if d.over_one_year else "no",
            d.regime,
            _num(d.quantity),
            _num(d.proceeds_eur),
            _num(d.cost_eur),
            _num(d.fee_eur),
            _num(d.gain_eur),
        ]
        for d in summary.disposals
    ]
    return _csv(header, rows)


def lots_csv(result) -> str:
    """Open lots of a `FifoResult` — the end-of-year holdings with cost basis."""
    header = [
        "wallet",
        "instrument_id",
        "acquired",
        "quantity",
        "cost_eur",
        "source_tx_id",
        "origin_tx_id",
    ]
    rows = [
        [
            lot.wallet,
            lot.instrument_id,
            lot.acquired.date().isoformat(),
            _num(lot.quantity),
            _num(lot.cost_eur),
            lot.source_tx_id,
            lot.origin_tx_id,
        ]
        for lot in result.open_lots
    ]
    return _csv(header, rows)


def blockpit_csv(transactions, instruments=(), accounts=()) -> str:
    """Blockpit's universal import template.

    `instruments` and `accounts` only resolve ids to symbols and venue names;
    without them the columns fall back to the raw ids.
    """
    symbols, names = _lookup(instruments, accounts)
    header = [
        "Date (UTC)",
        "Integration Name",
        "Label",
        "Outgoing Asset",
        "Outgoing Amount",
        "Incoming Asset",
        "Incoming Amount",
        "Fee Asset",
        "Fee Amount",
        "Trx. ID",
        "Comments",
    ]
    rows = []
    for tx in transactions:
        symbol = symbols.get(tx.instrument_id, str(tx.instrument_id or ""))
        incoming_asset, incoming, outgoing_asset, outgoing = _legs(tx, symbol)
        rows.append(
            [
                tx.ts.strftime("%Y-%m-%d %H:%M:%S"),
                names.get(tx.account_id, str(tx.account_id)),
                _BLOCKPIT_LABEL.get(tx.type, "Other"),
                outgoing_asset,
                outgoing,
                incoming_asset,
                incoming,
                "EUR" if tx.fee_eur else "",
                _num(tx.fee_eur) if tx.fee_eur else "",
                tx.external_id or str(tx.id),
                tx.note or "",
            ]
        )
    return _csv(header, rows)


def cointracking_csv(transactions, instruments=(), accounts=()) -> str:
    """CoinTracking's custom CSV import. Buy is the incoming leg, Sell the
    outgoing one — the same legs Blockpit calls Incoming and Outgoing.
    """
    symbols, names = _lookup(instruments, accounts)
    header = [
        "Type",
        "Buy Amount",
        "Buy Currency",
        "Sell Amount",
        "Sell Currency",
        "Fee",
        "Fee Currency",
        "Exchange",
        "Trade-Group",
        "Comment",
        "Date",
    ]
    rows = []
    for tx in transactions:
        symbol = symbols.get(tx.instrument_id, str(tx.instrument_id or ""))
        buy_asset, buy, sell_asset, sell = _legs(tx, symbol)
        rows.append(
            [
                _COINTRACKING_TYPE.get(tx.type, "Other"),
                buy,
                buy_asset,
                sell,
                sell_asset,
                _num(tx.fee_eur) if tx.fee_eur else "",
                "EUR" if tx.fee_eur else "",
                names.get(tx.account_id, str(tx.account_id)),
                "",
                tx.note or "",
                tx.ts.strftime("%Y-%m-%d %H:%M:%S"),
            ]
        )
    return _csv(header, rows)
