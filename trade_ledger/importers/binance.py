"""Binance "transaction history" CSV -> TxDraft rows.

Header: `User_ID, UTC_Time, Account, Operation, Coin, Change, Remark`. Rows
sharing a `UTC_Time` are one event: a trade's legs (`Buy`, `Transaction
Buy`, `Transaction Spend`, `Transaction Revenue`, plus an optional `Fee`
leg) collapse into one BUY/SELL; everything else is a single-row event.

Binance gives no per-row id, so `external_id` is left unset here and
`upsert_transactions`'s built-in content hash (ts|type|symbol|quantity|
amount_eur) stands in for it — reusing that instead of hashing our own.
"""

from __future__ import annotations

from decimal import Decimal

from ..enums import AssetClass, TxSource, TxType
from ..ledger import TxDraft
from . import ImportResult, RowError
from ._common import dec, parse_utc, read_rows

_FIAT_LIKE = {"EUR", "USD", "USDT", "USDC", "BUSD", "TUSD"}
_TRADE_OPS = {"Buy", "Transaction Buy", "Transaction Spend", "Transaction Revenue"}


def _trade_draft(ts, legs: list[dict], fee_amount: Decimal, fee_ccy: str | None) -> TxDraft:
    positive = [r for r in legs if dec(r["Change"]) > 0]
    negative = [r for r in legs if dec(r["Change"]) < 0]
    if len(positive) != 1 or len(negative) != 1:
        raise ValueError("expected exactly one incoming and one outgoing trade leg")

    in_row, out_row = positive[0], negative[0]
    in_coin = in_row["Coin"].strip().upper()
    out_coin = out_row["Coin"].strip().upper()
    in_amount = abs(dec(in_row["Change"]))
    out_amount = abs(dec(out_row["Change"]))

    if out_coin in _FIAT_LIKE:
        tx_type, symbol, quantity, quote_ccy, quote_amount = (
            TxType.BUY, in_coin, in_amount, out_coin, out_amount,
        )
    elif in_coin in _FIAT_LIKE:
        tx_type, symbol, quantity, quote_ccy, quote_amount = (
            TxType.SELL, out_coin, out_amount, in_coin, in_amount,
        )
    else:
        # crypto-to-crypto: no fiat leg at all, treat the incoming coin as bought
        tx_type, symbol, quantity, quote_ccy, quote_amount = (
            TxType.BUY, in_coin, in_amount, out_coin, out_amount,
        )

    if quote_ccy == "EUR":
        amount_eur, fx_source = quote_amount, None
    else:
        amount_eur, fx_source = Decimal(0), "pending"

    fee_eur = fee_amount if fee_ccy == "EUR" else Decimal(0)
    fee = Decimal(0) if fee_ccy == "EUR" else fee_amount
    fee_ccy_kept = None if fee_ccy == "EUR" else fee_ccy

    return TxDraft(
        ts=ts,
        type=tx_type,
        quantity=quantity,
        price_ccy=quote_ccy,
        fee=fee,
        fee_ccy=fee_ccy_kept,
        fee_eur=fee_eur,
        amount_eur=amount_eur,
        fx_source=fx_source,
        source=TxSource.CSV,
        instrument_symbol=symbol,
        asset_class=AssetClass.CRYPTO,
    )


def _fee_only_draft(ts, fee_amount: Decimal, fee_ccy: str | None) -> TxDraft:
    """A `Fee` row with no matching Buy/Sell leg at that timestamp — Binance
    charges these standalone (e.g. a BNB fee-burn credit). `TxType.FEE`'s
    cash effect is read off `amount_eur` (see `ledger.cash_delta_eur`), not
    `fee_eur`, so that's where the resolved amount goes.
    """
    if fee_ccy == "EUR":
        return TxDraft(ts=ts, type=TxType.FEE, amount_eur=fee_amount, source=TxSource.CSV)
    return TxDraft(
        ts=ts,
        type=TxType.FEE,
        amount_eur=Decimal(0),
        fee=fee_amount,
        fee_ccy=fee_ccy,
        fx_source="pending",
        source=TxSource.CSV,
    )


def _other_draft(ts, row: dict) -> TxDraft:
    op = row["Operation"]
    coin = row["Coin"].strip().upper()
    quantity = abs(dec(row["Change"]))

    if op == "Deposit":
        tx_type = TxType.DEPOSIT if coin in ("EUR", "USD") else TxType.TRANSFER_IN
    elif op == "Withdraw":
        tx_type = TxType.WITHDRAWAL if coin in ("EUR", "USD") else TxType.TRANSFER_OUT
    elif op == "Staking Rewards" or op.startswith("Simple Earn"):
        tx_type = TxType.STAKING_REWARD
    elif op == "Distribution":
        tx_type = TxType.AIRDROP
    else:
        raise ValueError(f"unrecognised operation: {op!r}")

    if tx_type in (TxType.DEPOSIT, TxType.WITHDRAWAL):
        return TxDraft(ts=ts, type=tx_type, amount_eur=quantity, source=TxSource.CSV)

    # crypto transfer/reward/airdrop: no EUR price in this export
    fx_source = "pending" if tx_type in (TxType.STAKING_REWARD, TxType.AIRDROP) else None
    return TxDraft(
        ts=ts,
        type=tx_type,
        quantity=quantity,
        amount_eur=Decimal(0),
        fx_source=fx_source,
        source=TxSource.CSV,
        instrument_symbol=coin,
        asset_class=AssetClass.CRYPTO,
    )


def parse(data: bytes) -> ImportResult:
    groups: dict[str, list[tuple[int, dict]]] = {}
    for row_num, row in enumerate(read_rows(data), start=2):
        groups.setdefault(row["UTC_Time"], []).append((row_num, row))

    drafts: list[TxDraft] = []
    errors: list[RowError] = []

    for utc_time, entries in groups.items():
        first_row = entries[0][0]
        rows = [r for _, r in entries]
        try:
            ts = parse_utc(utc_time)
            trade_legs = [r for r in rows if r["Operation"] in _TRADE_OPS]
            fee_legs = [r for r in rows if r["Operation"] == "Fee"]
            other_rows = [
                r for r in rows if r["Operation"] not in _TRADE_OPS and r["Operation"] != "Fee"
            ]

            fee_amount = sum((abs(dec(r["Change"])) for r in fee_legs), Decimal(0))
            fee_ccy = fee_legs[0]["Coin"].strip().upper() if fee_legs else None

            if trade_legs:
                drafts.append(_trade_draft(ts, trade_legs, fee_amount, fee_ccy))
            elif fee_legs:
                drafts.append(_fee_only_draft(ts, fee_amount, fee_ccy))
            for row in other_rows:
                drafts.append(_other_draft(ts, row))
        except (KeyError, ValueError, ArithmeticError) as exc:
            errors.append(RowError(row=first_row, reason=str(exc)))

    return ImportResult(drafts=drafts, errors=errors)
