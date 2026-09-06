"""FIFO lot tracking per tax wallet, with holding periods and per-disposal gains.

Legal background (checked 2026-09-06): BMF letter of 6 March 2025 on the income
tax treatment of Kryptowerte. FIFO is applied per wallet (each address or
exchange account is its own pool). Section 23 Abs. 1 Nr. 2 EStG makes a disposal
tax-free once the asset was held for more than one year, so the comparison is
strictly greater than acquisition date + 1 year. Fees paid on an acquisition are
Anschaffungsnebenkosten and raise the cost basis; fees paid on a disposal are
Werbungskosten and are deducted from the proceeds.
VERIFY: the Rn. numbers usually quoted for these points come from secondary
sources, not from a reading of the letter itself — confirm before citing them
anywhere user-facing.

`run_fifo` is a pure function over lists of `Transaction`-shaped objects; it
never touches the session.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from dateutil.relativedelta import relativedelta

from ..enums import TaxRegime, TxType
from .regime import regime_for

CENT = Decimal("0.01")

# Types that move lots. Everything else (cash rows, dividends, fees) is ignored.
_HANDLED = (
    TxType.BUY,
    TxType.SELL,
    TxType.TRANSFER_IN,
    TxType.TRANSFER_OUT,
    TxType.STAKING_REWARD,
    TxType.AIRDROP,
    TxType.SPLIT,
)


class InsufficientLots(Exception):
    """Raised in strict mode when a disposal wants more than the wallet holds."""

    def __init__(self, wallet: str, instrument_id: int, ts: datetime, missing: Decimal):
        self.wallet = wallet
        self.instrument_id = instrument_id
        self.ts = ts
        self.missing = missing
        super().__init__(
            f"{wallet}/{instrument_id} at {ts.isoformat()}: {missing} short of the disposal"
        )


@dataclass
class Lot:
    wallet: str
    instrument_id: int
    acquired: datetime
    quantity: Decimal
    cost_eur: Decimal
    source_tx_id: int
    origin_tx_id: int  # first acquisition — survives transfers


@dataclass
class Disposal:
    wallet: str
    instrument_id: int
    tx_id: int
    ts: datetime
    quantity: Decimal
    proceeds_eur: Decimal
    cost_eur: Decimal
    fee_eur: Decimal
    gain_eur: Decimal
    acquired: datetime
    holding_days: int
    over_one_year: bool
    regime: TaxRegime


@dataclass
class FifoResult:
    open_lots: list[Lot] = field(default_factory=list)
    disposals: list[Disposal] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _sort_key(tx):
    # A transfer_out must be processed before the transfer_in it feeds, even
    # when both carry the same timestamp and sit in different wallets.
    rank = 1 if tx.type == TxType.TRANSFER_IN else 0
    return (tx.ts, rank, getattr(tx, "id", None) or 0)


def _cost_share(lot: Lot, quantity: Decimal) -> Decimal:
    """Cost basis carried by `quantity` of `lot`; the exact rest when it is emptied."""
    if quantity >= lot.quantity:
        return lot.cost_eur
    return min(_cents(lot.cost_eur * quantity / lot.quantity), lot.cost_eur)


def run_fifo(transactions, instruments, accounts, *, strict: bool = False) -> FifoResult:
    """Replay `transactions` as FIFO lots, one pool per (tax wallet, instrument)."""
    instrument_by_id = {i.id: i for i in instruments}
    account_by_id = {a.id: a for a in accounts}
    pools: dict[tuple[str, int], list[Lot]] = defaultdict(list)
    in_transit: dict[str, list[Lot]] = defaultdict(list)
    result = FifoResult()

    def take(pool, key, quantity, tx) -> list[tuple[Lot, Decimal, Decimal]]:
        """Pop `quantity` off the front of `pool` as (lot, quantity, cost) triples."""
        taken: list[tuple[Lot, Decimal, Decimal]] = []
        left = quantity
        while left > 0 and pool:
            lot = pool[0]
            part = min(lot.quantity, left)
            cost = _cost_share(lot, part)
            taken.append((lot, part, cost))
            lot.quantity -= part
            lot.cost_eur -= cost
            left -= part
            if lot.quantity <= 0:
                pool.pop(0)
        if left > 0:
            if strict:
                raise InsufficientLots(key[0], key[1], tx.ts, left)
            result.warnings.append(
                f"tx {tx.id}: only partial lots for {left} of {key[0]}/{key[1]}, "
                "zero-cost lot assumed"
            )
            synthetic = Lot(key[0], key[1], tx.ts, left, Decimal(0), tx.id, tx.id)
            taken.append((synthetic, left, Decimal(0)))
        return taken

    for tx in sorted(transactions, key=_sort_key):
        if tx.type not in _HANDLED or tx.instrument_id is None:
            continue
        account = account_by_id.get(tx.account_id)
        if account is None:
            result.warnings.append(f"tx {tx.id} references unknown account {tx.account_id}")
            continue
        instrument = instrument_by_id.get(tx.instrument_id)

        amount = tx.amount_eur
        if getattr(tx, "fx_source", None) == "pending":
            result.warnings.append(f"tx {tx.id} has no EUR value (fx pending)")
            amount = Decimal(0)

        key = (account.tax_wallet, tx.instrument_id)
        pool = pools[key]

        if tx.type == TxType.BUY:
            # Fees on acquisition are Anschaffungsnebenkosten: part of the cost.
            pool.append(Lot(key[0], key[1], tx.ts, tx.quantity, amount + tx.fee_eur, tx.id, tx.id))

        elif tx.type in (TxType.STAKING_REWARD, TxType.AIRDROP):
            pool.append(Lot(key[0], key[1], tx.ts, tx.quantity, amount, tx.id, tx.id))

        elif tx.type == TxType.SELL:
            taken = take(pool, key, tx.quantity, tx)
            regime = regime_for(tx, instrument, account)
            allocated_proceeds = Decimal(0)
            allocated_fee = Decimal(0)
            for index, (lot, part, cost) in enumerate(taken):
                if index == len(taken) - 1:  # remainder to the last lot, no dust lost
                    proceeds = amount - allocated_proceeds
                    fee = tx.fee_eur - allocated_fee
                else:
                    proceeds = _cents(amount * part / tx.quantity)
                    fee = _cents(tx.fee_eur * part / tx.quantity)
                allocated_proceeds += proceeds
                allocated_fee += fee
                result.disposals.append(
                    Disposal(
                        wallet=key[0],
                        instrument_id=key[1],
                        tx_id=tx.id,
                        ts=tx.ts,
                        quantity=part,
                        proceeds_eur=proceeds,
                        cost_eur=cost,
                        # Fees on disposal are Werbungskosten: deducted.
                        fee_eur=fee,
                        gain_eur=proceeds - cost - fee,
                        acquired=lot.acquired,
                        holding_days=(tx.ts.date() - lot.acquired.date()).days,
                        over_one_year=tx.ts.date() > lot.acquired.date() + relativedelta(years=1),
                        regime=regime,
                    )
                )

        elif tx.type == TxType.TRANSFER_OUT:
            carried = [
                replace(lot, quantity=part, cost_eur=cost, source_tx_id=tx.id)
                for lot, part, cost in take(pool, key, tx.quantity, tx)
            ]
            if tx.link_id:
                in_transit[tx.link_id].extend(carried)
            else:
                result.warnings.append(f"tx {tx.id}: unlinked transfer_out, lots dropped")

        elif tx.type == TxType.TRANSFER_IN:
            carried = in_transit.pop(tx.link_id, None) if tx.link_id else None
            if carried:
                # Same acquisition dates and costs, new wallet — the holding
                # period is not restarted by moving coins between own wallets.
                pool.extend(
                    replace(lot, wallet=key[0], instrument_id=key[1], source_tx_id=tx.id)
                    for lot in carried
                )
                pool.sort(key=lambda lot: (lot.acquired, lot.origin_tx_id))
            else:
                result.warnings.append(f"tx {tx.id}: unlinked transfer_in, cost basis unknown")
                pool.append(Lot(key[0], key[1], tx.ts, tx.quantity, amount, tx.id, tx.id))

        elif tx.type == TxType.SPLIT:
            total = sum((lot.quantity for lot in pool), Decimal(0))
            if total <= 0:
                result.warnings.append(f"tx {tx.id}: split with no open lots, ignored")
                continue
            allocated = Decimal(0)
            for index, lot in enumerate(pool):
                extra = (
                    tx.quantity - allocated
                    if index == len(pool) - 1
                    else tx.quantity * lot.quantity / total
                )
                allocated += extra
                lot.quantity += extra  # cost and acquisition date unchanged

    for pool in pools.values():
        result.open_lots.extend(pool)
    for link_id, carried in in_transit.items():
        if not carried:
            continue
        result.warnings.append(
            f"transfer {link_id} never arrived, {len(carried)} lot(s) in transit"
        )
        result.open_lots.extend(carried)
    return result
