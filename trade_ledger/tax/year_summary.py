"""One tax year, bucketed into the German forms.

Legal background (checked 2026-09-06, secondary sources — VERIFY):

* Section 23 EStG: a crypto disposal inside the one-year holding period is a
  privates Veraeusserungsgeschaeft. The Freigrenze is 1.000 EUR from VZ 2024
  and is all-or-nothing: at 999 EUR nothing is taxed, at 1.000 EUR everything.
* Section 22 Nr. 3 EStG: staking rewards and airdrops received for a service
  are sonstige Leistungen with a 256 EUR Freigrenze, likewise all-or-nothing.
* Section 20 EStG: shares, Termingeschaefte and dividends/interest, with a
  1.000 EUR Sparerpauschbetrag. The JStG 2024 removed the Termingeschaeft loss
  cap from VZ 2025 onwards.
* Section 16 ff. InvStG: funds and ETFs are reported on Anlage KAP-INV with a
  Teilfreistellung of 30/15/60/80/0 % by fund type. The gross figures go on the
  form; the Finanzamt applies the Teilfreistellung, so `sale_gain`,
  `sale_loss`, `distributions` and `vorabpauschale` here are all gross.

`summarize` is the only entry point. It runs FIFO once over every transaction
up to 31.12 of `year`, so the open lots it leaves behind are exactly the
end-of-year holdings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import AssetClass, FundType, TaxRegime, TxType
from ..models import Account, Instrument, Transaction
from ..prices.service import PriceMissing, get_close_eur
from .fifo import Disposal, FifoResult, run_fifo
from .regime import regime_for
from .vorabpauschale import basiszins_for, compute, months_factor

ZERO = Decimal(0)
CENT = Decimal("0.01")

P23_FREIGRENZE = Decimal(1000)
P22_FREIGRENZE = Decimal(256)
SPARERPAUSCHBETRAG = Decimal(1000)

TEILFREISTELLUNG = {
    FundType.AKTIEN: 30,
    FundType.MISCH: 15,
    FundType.IMMO: 60,
    FundType.IMMO_AUSLAND: 80,
    FundType.SONSTIGE: 0,
}

# 1 January and 31 December are never trading days, so the "value at the
# beginning/end of the year" is the nearest cached close within a week.
_PRICE_SEARCH_DAYS = 7


@dataclass
class P23Summary:
    """Section 23 EStG. `taxable_*` cover disposals inside the one-year period
    only; gains and losses are split before Werbungskosten so that
    `net == taxable_gains - taxable_losses - werbungskosten == proceeds - cost
    - werbungskosten`, which is what Anlage SO lines 48-51 want.
    """

    taxable_gains: Decimal = ZERO
    taxable_losses: Decimal = ZERO
    taxfree_gains: Decimal = ZERO
    werbungskosten: Decimal = ZERO
    proceeds: Decimal = ZERO
    cost: Decimal = ZERO
    net: Decimal = ZERO
    freigrenze: Decimal = P23_FREIGRENZE
    exceeded: bool = False
    disposals: list[Disposal] = field(default_factory=list)


@dataclass
class P22Summary:
    """Section 22 Nr. 3 EStG. `werbungskosten` has no source in the ledger yet
    (no expense rows), so it is always 0 and `net == income`.
    """

    income: Decimal = ZERO
    werbungskosten: Decimal = ZERO
    net: Decimal = ZERO
    freigrenze: Decimal = P22_FREIGRENZE
    exceeded: bool = False
    items: list[Transaction] = field(default_factory=list)


@dataclass
class P20Summary:
    """Section 20 EStG. Gains and losses are net of transaction fees here
    (Section 20 Abs. 4), unlike Section 23 where the form has its own
    Werbungskosten line. `sonstige_net` is dividends plus interest — no
    Werbungskosten are deductible under Section 20 Abs. 9, only the
    Sparerpauschbetrag, which the Finanzamt applies. `other_losses` is KAP line
    22: Termin losses plus fund sale losses, i.e. every loss that is not a
    share loss.
    """

    aktien_gains: Decimal = ZERO
    aktien_losses: Decimal = ZERO
    termin_gains: Decimal = ZERO
    termin_losses: Decimal = ZERO
    dividends: Decimal = ZERO
    interest: Decimal = ZERO
    withholding_tax: Decimal = ZERO
    sonstige_net: Decimal = ZERO
    other_losses: Decimal = ZERO
    total_foreign: Decimal = ZERO
    sparerpauschbetrag: Decimal = SPARERPAUSCHBETRAG


@dataclass
class InvRow:
    """One fund or ETF on Anlage KAP-INV. All amounts gross."""

    instrument: Instrument
    fund_type: FundType
    teilfreistellung_pct: int
    distributions: Decimal = ZERO
    vorabpauschale: Decimal = ZERO
    sale_gain: Decimal = ZERO
    sale_loss: Decimal = ZERO


class Holding(NamedTuple):
    wallet: str
    symbol: str
    quantity: Decimal
    value_eur: Decimal | None


class VenueRow(NamedTuple):
    account: str
    gross_proceeds: Decimal
    gross_acquisitions: Decimal
    disposals_count: int
    deposits: Decimal
    withdrawals: Decimal


@dataclass
class YearSummary:
    year: int
    p23: P23Summary
    p22: P22Summary
    p20: P20Summary
    inv: list[InvRow] = field(default_factory=list)
    eoy_holdings: list[Holding] = field(default_factory=list)
    venues: list[VenueRow] = field(default_factory=list)
    disposals: list[Disposal] = field(default_factory=list)
    fifo: FifoResult = field(default_factory=FifoResult)
    symbols: dict[int, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _sum(values) -> Decimal:
    return sum(values, ZERO)


def _is_fund(instrument: Instrument | None) -> bool:
    """True for anything reported on Anlage KAP-INV rather than Anlage KAP."""
    if instrument is None:
        return False
    if instrument.tax_regime:
        return instrument.tax_regime == TaxRegime.P20_INV
    return instrument.asset_class == AssetClass.ETF


def _close_eur_near(
    session: Session, instrument: Instrument, on: date, step_days: int
) -> Decimal | None:
    """Close in EUR on `on`, else the nearest cached one within a week, walking
    forwards (`step_days=1`) or backwards (`step_days=-1`).
    """
    for offset in range(_PRICE_SEARCH_DAYS + 1):
        try:
            close = get_close_eur(session, instrument, on + timedelta(days=offset * step_days))
        except PriceMissing:
            continue
        if close is not None:
            return close
    return None


def summarize(session: Session, year: int, account_ids: list[int] | None = None) -> YearSummary:
    """Everything the forms, the exports and the CLI need for one tax year."""
    start = datetime(year, 1, 1, tzinfo=UTC)
    end = datetime(year, 12, 31, 23, 59, 59, tzinfo=UTC)

    stmt = select(Transaction).where(Transaction.ts <= end)
    if account_ids is not None:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    transactions = list(session.execute(stmt.order_by(Transaction.ts)).scalars())
    accounts = list(session.execute(select(Account)).scalars())
    instruments = list(session.execute(select(Instrument)).scalars())
    instrument_by_id = {i.id: i for i in instruments}
    account_by_id = {a.id: a for a in accounts}

    fifo = run_fifo(transactions, instruments, accounts)
    warnings = list(fifo.warnings)
    in_year = [t for t in transactions if start <= t.ts <= end]
    disposals = [d for d in fifo.disposals if start <= d.ts <= end]

    p23 = _p23(disposals)
    p22 = _p22(in_year, instrument_by_id, account_by_id)
    inv = _inv(session, year, disposals, in_year, fifo, instrument_by_id, warnings)
    p20 = _p20(disposals, in_year, instrument_by_id, inv)

    return YearSummary(
        year=year,
        p23=p23,
        p22=p22,
        p20=p20,
        inv=inv,
        eoy_holdings=_eoy_holdings(session, year, fifo, instrument_by_id, warnings),
        venues=_venues(in_year, account_by_id),
        disposals=disposals,
        fifo=fifo,
        symbols={i.id: i.symbol for i in instruments},
        warnings=warnings,
    )


def _p23(disposals: list[Disposal]) -> P23Summary:
    everything = [d for d in disposals if d.regime == TaxRegime.P23]
    taxable = [d for d in everything if not d.over_one_year]
    gross = [(d.proceeds_eur - d.cost_eur) for d in taxable]
    gains = _sum(g for g in gross if g > 0)
    losses = _sum(-g for g in gross if g < 0)
    werbungskosten = _sum(d.fee_eur for d in taxable)
    net = gains - losses - werbungskosten
    return P23Summary(
        taxable_gains=gains,
        taxable_losses=losses,
        taxfree_gains=_sum(d.gain_eur for d in everything if d.over_one_year),
        werbungskosten=werbungskosten,
        proceeds=_sum(d.proceeds_eur for d in taxable),
        cost=_sum(d.cost_eur for d in taxable),
        net=net,
        exceeded=net >= P23_FREIGRENZE,
        disposals=everything,
    )


def _p22(in_year, instrument_by_id, account_by_id) -> P22Summary:
    items = [
        t
        for t in in_year
        if t.type in (TxType.STAKING_REWARD, TxType.AIRDROP)
        and regime_for(t, instrument_by_id.get(t.instrument_id), account_by_id.get(t.account_id))
        == TaxRegime.P22
    ]
    income = _sum(t.amount_eur for t in items)
    return P22Summary(income=income, net=income, exceeded=income >= P22_FREIGRENZE, items=items)


def _inv(session, year, disposals, in_year, fifo, instrument_by_id, warnings) -> list[InvRow]:
    rows: dict[int, InvRow] = {}

    def row_for(instrument: Instrument) -> InvRow:
        if instrument.id not in rows:
            fund_type = FundType(instrument.fund_type or FundType.SONSTIGE)
            rows[instrument.id] = InvRow(
                instrument=instrument,
                fund_type=fund_type,
                teilfreistellung_pct=TEILFREISTELLUNG[fund_type],
            )
        return rows[instrument.id]

    for disposal in disposals:
        if disposal.regime != TaxRegime.P20_INV:
            continue
        row = row_for(instrument_by_id[disposal.instrument_id])
        if disposal.gain_eur >= 0:
            row.sale_gain += disposal.gain_eur
        else:
            row.sale_loss += -disposal.gain_eur

    # A distribution from a fund belongs on KAP-INV, not on KAP as a dividend.
    for tx in in_year:
        instrument = instrument_by_id.get(tx.instrument_id)
        if tx.type == TxType.DIVIDEND and _is_fund(instrument):
            row_for(instrument).distributions += tx.amount_eur

    basiszins = basiszins_for(session, year)
    lots_by_instrument: dict[int, list] = {}
    for lot in fifo.open_lots:
        if _is_fund(instrument_by_id.get(lot.instrument_id)) and lot.quantity > 0:
            lots_by_instrument.setdefault(lot.instrument_id, []).append(lot)

    for instrument_id, lots in lots_by_instrument.items():
        instrument = instrument_by_id[instrument_id]
        row = row_for(instrument)
        if basiszins is None:
            warnings.append(f"no Basiszins for {year}, Vorabpauschale set to 0")
            continue
        jan1 = _close_eur_near(session, instrument, date(year, 1, 1), 1)
        dec31 = _close_eur_near(session, instrument, date(year, 12, 31), -1)
        if jan1 is None or dec31 is None:
            warnings.append(
                f"{instrument.symbol}: no close near "
                f"{'01.01.' if jan1 is None else '31.12.'}{year}, Vorabpauschale set to 0"
            )
            continue
        total_qty = _sum(lot.quantity for lot in lots)
        # The year's distributions are shared out over the lots by size.
        paid = row.distributions
        for lot in lots:
            share = paid * lot.quantity / total_qty if total_qty else ZERO
            factor = months_factor(lot.acquired.month if lot.acquired.year == year else None)
            row.vorabpauschale += compute(lot.quantity, jan1, dec31, share, basiszins, factor)

    return sorted(rows.values(), key=lambda r: r.instrument.symbol)


def _p20(disposals, in_year, instrument_by_id, inv: list[InvRow]) -> P20Summary:
    def split(regime) -> tuple[Decimal, Decimal]:
        gains = [d.gain_eur for d in disposals if d.regime == regime]
        return _sum(g for g in gains if g > 0), _sum(-g for g in gains if g < 0)

    aktien_gains, aktien_losses = split(TaxRegime.P20_AKTIEN)
    termin_gains, termin_losses = split(TaxRegime.P20_TERMIN)

    cash = [
        t
        for t in in_year
        if t.type in (TxType.DIVIDEND, TxType.INTEREST)
        and not _is_fund(instrument_by_id.get(t.instrument_id))
    ]
    dividends = _sum(t.amount_eur for t in cash if t.type == TxType.DIVIDEND)
    interest = _sum(t.amount_eur for t in cash if t.type == TxType.INTEREST)
    # Withholding on fund distributions is creditable too, so KAP line 41 takes
    # every dividend and interest row, fund or not.
    withholding = _sum(
        t.withholding_tax_eur for t in in_year if t.type in (TxType.DIVIDEND, TxType.INTEREST)
    )
    inv_net = _sum(r.sale_gain - r.sale_loss for r in inv)

    return P20Summary(
        aktien_gains=aktien_gains,
        aktien_losses=aktien_losses,
        termin_gains=termin_gains,
        termin_losses=termin_losses,
        dividends=dividends,
        interest=interest,
        withholding_tax=withholding,
        sonstige_net=dividends + interest,
        other_losses=termin_losses + _sum(r.sale_loss for r in inv),
        total_foreign=(
            dividends
            + interest
            + (aktien_gains - aktien_losses)
            + (termin_gains - termin_losses)
            + inv_net
        ),
    )


def _eoy_holdings(session, year, fifo, instrument_by_id, warnings) -> list[Holding]:
    positions: dict[tuple[str, int], Decimal] = {}
    for lot in fifo.open_lots:
        key = (lot.wallet, lot.instrument_id)
        positions[key] = positions.get(key, ZERO) + lot.quantity

    holdings: list[Holding] = []
    unpriced: set[int] = set()
    for (wallet, instrument_id), quantity in positions.items():
        instrument = instrument_by_id.get(instrument_id)
        if quantity <= 0 or instrument is None:
            continue
        close = _close_eur_near(session, instrument, date(year, 12, 31), -1)
        if close is None and instrument_id not in unpriced:
            unpriced.add(instrument_id)
            warnings.append(f"{instrument.symbol}: no close near 31.12.{year}, holding not valued")
        value = None if close is None else (close * quantity).quantize(CENT, ROUND_HALF_UP)
        holdings.append(Holding(wallet, instrument.symbol, quantity, value))
    return sorted(holdings, key=lambda h: (h.wallet, h.symbol))


def _venues(in_year, account_by_id) -> list[VenueRow]:
    by_account: dict[int, list] = {}
    for tx in in_year:
        by_account.setdefault(tx.account_id, []).append(tx)

    rows = []
    for account_id, txs in by_account.items():
        account = account_by_id.get(account_id)
        rows.append(
            VenueRow(
                account=account.name if account else str(account_id),
                gross_proceeds=_sum(t.amount_eur for t in txs if t.type == TxType.SELL),
                gross_acquisitions=_sum(t.amount_eur for t in txs if t.type == TxType.BUY),
                disposals_count=sum(1 for t in txs if t.type == TxType.SELL),
                deposits=_sum(t.amount_eur for t in txs if t.type == TxType.DEPOSIT),
                withdrawals=_sum(t.amount_eur for t in txs if t.type == TxType.WITHDRAWAL),
            )
        )
    return sorted(rows, key=lambda r: r.account)
