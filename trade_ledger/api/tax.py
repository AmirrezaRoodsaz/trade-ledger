"""Tax year routes. Every response carries the disclaimer and the form status —
these numbers are a Berechnung, not a Steuererklaerung.

`mode` defaults to `live` here rather than to `paper` like the stats routes:
tax is about real money only. `mode=all` is allowed but mixes paper trades into
the figures, so it is never the default.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..enums import Mode, TaxRegime, TxType
from ..models import Account, Instrument, Transaction
from ..tax import anlage, exports
from ..tax.regime import regime_for
from ..tax.year_summary import YearSummary, summarize
from ._common import resolve_account_ids
from .schemas import BaseModel, Money

router = APIRouter(prefix="/tax")

DISCLAIMER = "Berechnung — mit Steuerberater prüfen"
FORM_STATUS = "Formstand: VZ 2025, geprüft 2026-09-06"


class TaxResponse(BaseModel):
    disclaimer: str = DISCLAIMER
    form_status: str = FORM_STATUS


class P23Out(BaseModel):
    taxable_gains: Money
    taxable_losses: Money
    taxfree_gains: Money
    werbungskosten: Money
    proceeds: Money
    cost: Money
    net: Money
    freigrenze: Money
    exceeded: bool


class P22Out(BaseModel):
    income: Money
    werbungskosten: Money
    net: Money
    freigrenze: Money
    exceeded: bool


class P20Out(BaseModel):
    aktien_gains: Money
    aktien_losses: Money
    termin_gains: Money
    termin_losses: Money
    dividends: Money
    interest: Money
    withholding_tax: Money
    sonstige_net: Money
    other_losses: Money
    total_foreign: Money
    sparerpauschbetrag: Money


class InvOut(BaseModel):
    symbol: str
    fund_type: str
    teilfreistellung_pct: int
    distributions: Money
    vorabpauschale: Money
    sale_gain: Money
    sale_loss: Money


class HoldingOut(BaseModel):
    wallet: str
    symbol: str
    quantity: Money
    value_eur: Money | None


class VenueOut(BaseModel):
    account: str
    gross_proceeds: Money
    gross_acquisitions: Money
    disposals_count: int
    deposits: Money
    withdrawals: Money


class DisposalOut(BaseModel):
    wallet: str
    instrument_id: int
    symbol: str
    tx_id: int
    ts: datetime
    acquired: datetime
    holding_days: int
    over_one_year: bool
    regime: TaxRegime
    quantity: Money
    proceeds_eur: Money
    cost_eur: Money
    fee_eur: Money
    # Net of the disposal fee, unlike `p23.taxable_gains`, which is gross so
    # that the fee can go on its own Werbungskosten line of Anlage SO.
    gain_eur: Money


class LotOut(BaseModel):
    wallet: str
    instrument_id: int
    symbol: str
    acquired: datetime
    quantity: Money
    cost_eur: Money
    # The regime a disposal of this lot would fall under. Only `p23` has a
    # twelve-month Spekulationsfrist, so the UI shows a countdown for it alone.
    regime: TaxRegime


class AnlageLineOut(BaseModel):
    form: str
    zeile: int
    label: str
    value: str
    note: str


class YearsOut(TaxResponse):
    years: list[int]


class SummaryOut(TaxResponse):
    year: int
    p23: P23Out
    p22: P22Out
    p20: P20Out
    inv: list[InvOut]
    eoy_holdings: list[HoldingOut]
    venues: list[VenueOut]
    warnings: list[str]


class DisposalsOut(TaxResponse):
    disposals: list[DisposalOut]


class LotsOut(TaxResponse):
    lots: list[LotOut]


class AnlageOut(TaxResponse):
    vz: int
    lines: list[AnlageLineOut]
    note: str | None = None


class HoldingsOut(TaxResponse):
    eoy_holdings: list[HoldingOut]


class VenuesOut(TaxResponse):
    venues: list[VenueOut]


class WarningsOut(TaxResponse):
    warnings: list[str]


def _account_ids(session: Session, mode: str, account_id: list[int] | None) -> list[int]:
    """The same mode intersection every stats route uses: explicit ids are
    still filtered by `mode`, so a paper account asked for under `mode=live`
    contributes nothing rather than leaking paper trades into a tax figure.
    """
    return resolve_account_ids(session, account_id, mode)


def _summary(session: Session, year: int, mode: str, account_id: list[int] | None) -> YearSummary:
    if year < 2009 or year > datetime.now(UTC).year + 1:
        raise HTTPException(status_code=422, detail="year out of range")
    return summarize(session, year, _account_ids(session, mode, account_id))


ModeQuery = Query(Mode.LIVE.value, pattern="^(live|paper|demo|all)$")


@router.get("/years", response_model=YearsOut)
def list_years(
    mode: str = ModeQuery,
    account_id: list[int] | None = Query(None),
    session: Session = Depends(get_session),
):
    stmt = select(func.substr(cast(Transaction.ts, String), 1, 4)).distinct()
    stmt = stmt.where(Transaction.account_id.in_(_account_ids(session, mode, account_id)))
    return YearsOut(years=sorted(int(row) for row in session.execute(stmt).scalars() if row))


def _inv_out(summary: YearSummary) -> list[InvOut]:
    return [
        InvOut(
            symbol=row.instrument.symbol,
            fund_type=row.fund_type,
            teilfreistellung_pct=row.teilfreistellung_pct,
            distributions=row.distributions,
            vorabpauschale=row.vorabpauschale,
            sale_gain=row.sale_gain,
            sale_loss=row.sale_loss,
        )
        for row in summary.inv
    ]


@router.get("/{year}/summary", response_model=SummaryOut)
def year_summary(
    year: int,
    mode: str = ModeQuery,
    account_id: list[int] | None = Query(None),
    session: Session = Depends(get_session),
):
    summary = _summary(session, year, mode, account_id)
    return SummaryOut(
        year=summary.year,
        p23=P23Out.model_validate(summary.p23),
        p22=P22Out.model_validate(summary.p22),
        p20=P20Out.model_validate(summary.p20),
        inv=_inv_out(summary),
        eoy_holdings=[HoldingOut.model_validate(h) for h in summary.eoy_holdings],
        venues=[VenueOut.model_validate(v) for v in summary.venues],
        warnings=summary.warnings,
    )


@router.get("/{year}/disposals", response_model=DisposalsOut)
def year_disposals(
    year: int,
    mode: str = ModeQuery,
    account_id: list[int] | None = Query(None),
    session: Session = Depends(get_session),
):
    summary = _summary(session, year, mode, account_id)
    return DisposalsOut(
        disposals=[
            DisposalOut(symbol=summary.symbols.get(d.instrument_id, ""), **vars(d))
            for d in summary.disposals
        ]
    )


@router.get("/{year}/lots", response_model=LotsOut)
def year_lots(
    year: int,
    mode: str = ModeQuery,
    account_id: list[int] | None = Query(None),
    session: Session = Depends(get_session),
):
    summary = _summary(session, year, mode, account_id)
    instruments = {i.id: i for i in session.execute(select(Instrument)).scalars()}
    # A lot is keyed by tax wallet, not account id; accounts that share a wallet
    # share a FIFO pool and, by construction, the same kind.
    accounts = {a.tax_wallet: a for a in session.execute(select(Account)).scalars()}

    def _regime(lot) -> TaxRegime:
        """`regime_for` on an acquisition-shaped view of the lot: the asset
        class and the account kind decide, exactly as they do for the SELL that
        will one day close it."""
        probe = SimpleNamespace(type=TxType.BUY, amount_eur=lot.cost_eur)
        return regime_for(probe, instruments.get(lot.instrument_id), accounts.get(lot.wallet))

    return LotsOut(
        lots=[
            LotOut(
                wallet=lot.wallet,
                instrument_id=lot.instrument_id,
                symbol=summary.symbols.get(lot.instrument_id, ""),
                acquired=lot.acquired,
                quantity=lot.quantity,
                cost_eur=lot.cost_eur,
                regime=_regime(lot),
            )
            for lot in summary.fifo.open_lots
        ]
    )


@router.get("/{year}/anlage", response_model=AnlageOut)
def year_anlage(
    year: int,
    mode: str = ModeQuery,
    account_id: list[int] | None = Query(None),
    session: Session = Depends(get_session),
):
    summary = _summary(session, year, mode, account_id)
    form_lines = anlage.lines(summary, year)
    note = None if form_lines else f"no line mapping for VZ {year}"
    return AnlageOut(vz=year, lines=form_lines, note=note)


@router.get("/{year}/eoy-holdings", response_model=HoldingsOut)
def year_eoy_holdings(
    year: int,
    mode: str = ModeQuery,
    account_id: list[int] | None = Query(None),
    session: Session = Depends(get_session),
):
    summary = _summary(session, year, mode, account_id)
    return HoldingsOut(eoy_holdings=[HoldingOut.model_validate(h) for h in summary.eoy_holdings])


@router.get("/{year}/venues", response_model=VenuesOut)
def year_venues(
    year: int,
    mode: str = ModeQuery,
    account_id: list[int] | None = Query(None),
    session: Session = Depends(get_session),
):
    summary = _summary(session, year, mode, account_id)
    return VenuesOut(venues=[VenueOut.model_validate(v) for v in summary.venues])


@router.get("/{year}/warnings", response_model=WarningsOut)
def year_warnings(
    year: int,
    mode: str = ModeQuery,
    account_id: list[int] | None = Query(None),
    session: Session = Depends(get_session),
):
    return WarningsOut(warnings=_summary(session, year, mode, account_id).warnings)


@router.get("/{year}/export")
def year_export(
    year: int,
    format: Literal["disposals", "lots", "blockpit", "cointracking"] = "disposals",
    mode: str = ModeQuery,
    account_id: list[int] | None = Query(None),
    session: Session = Depends(get_session),
):
    """CSV download. `disposals`/`lots` are this ledger's own columns;
    `blockpit`/`cointracking` are those tools' import templates.
    """
    summary = _summary(session, year, mode, account_id)
    if format == "disposals":
        body = exports.disposals_csv(summary)
    elif format == "lots":
        body = exports.lots_csv(summary.fifo)
    else:
        stmt = select(Transaction).where(
            Transaction.ts >= datetime(year, 1, 1, tzinfo=UTC),
            Transaction.ts <= datetime(year, 12, 31, 23, 59, 59, tzinfo=UTC),
            Transaction.account_id.in_(_account_ids(session, mode, account_id)),
        )
        transactions = list(session.execute(stmt.order_by(Transaction.ts)).scalars())
        instruments = list(session.execute(select(Instrument)).scalars())
        accounts = list(session.execute(select(Account)).scalars())
        writer = exports.blockpit_csv if format == "blockpit" else exports.cointracking_csv
        body = writer(transactions, instruments, accounts)

    return StreamingResponse(
        io.StringIO(body),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=tax-{year}-{format}.csv",
            # HTTP headers are latin-1 only, so the download carries an
            # ASCII transliteration of DISCLAIMER / FORM_STATUS.
            "X-Disclaimer": "Berechnung - mit Steuerberater pruefen",
            "X-Form-Status": "Formstand: VZ 2025, geprueft 2026-09-06",
        },
    )
