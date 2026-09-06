"""Weekly PDF report: headline stats, the equity curve, a monthly calendar,
open trades, open crypto lots and the two tax meters — one page, one mode.

Everything here is glue: `engine.analytics` does the trade math, `tax.fifo`
and `tax.year_summary` do the tax math, and this module only fetches rows,
shapes them and lays them out with fpdf2. Matplotlib runs headless (`Agg`,
set before `pyplot` is imported anywhere in the process) to a temp PNG that
fpdf2 then embeds.
"""

from __future__ import annotations

import calendar as calendar_mod
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from fpdf import FPDF, XPos, YPos
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import analytics
from ..enums import AssetClass, TradeStatus
from ..models import Account, Instrument, Trade, Transaction
from ..settings import get_settings
from ..tax.fifo import run_fifo
from ..tax.year_summary import (
    P23_FREIGRENZE,
    SPARERPAUSCHBETRAG,
    accounts_in_mode,
    summarize,
)
from .fmt import fmt_date, fmt_eur, fmt_num, fmt_pct, fmt_r, pdf_safe

DISCLAIMER = "Berechnung — mit Steuerberater prüfen"
FORM_STATUS = "Formstand: VZ 2025, geprüft 2026-09-06"

_LINE = 6
_STATS_ROWS = [
    ("Trades", lambda s: str(s.count)),
    ("Expectancy", lambda s: fmt_r(s.expectancy_r)),
    ("Win rate", lambda s: fmt_pct(s.win_rate)),
    ("Profit factor", lambda s: fmt_num(s.profit_factor) if s.profit_factor is not None else "-"),
    ("Max DD (R)", lambda s: fmt_r(s.max_dd_r)),
    ("Max DD (EUR)", lambda s: fmt_eur(s.max_dd_eur)),
    ("Adherence", lambda s: fmt_pct(s.adherence) if s.adherence is not None else "-"),
    ("SQN", lambda s: fmt_num(s.sqn) if s.sqn is not None else "-"),
]


def _trades_in_mode(session: Session, mode: str, stmt=None):
    """`stmt` (default: every `Trade`) restricted to `mode`'s accounts.

    A local stand-in for `api._common.mode_filter`: that helper lives in the
    `api` package, and importing it here would import `api/__init__`, whose
    router auto-discovery imports `api/reports.py`, which imports this module
    back — a circular import that only breaks a fresh process (`python -m
    trade_ledger.cli report ...`), not `pytest`, where something else has
    usually already finished importing `trade_ledger.api` first.
    """
    stmt = select(Trade) if stmt is None else stmt
    account_ids = accounts_in_mode(session, mode)
    return stmt if account_ids is None else stmt.where(Trade.account_id.in_(account_ids))


def build_weekly(session: Session, week_end: date, mode: str) -> Path:
    """Build the report and write it under `DATA_DIR/exports/reports/`, returning its path."""
    week_start = week_end - timedelta(days=6)
    trades = list(session.execute(_trades_in_mode(session, mode)).scalars())
    week_trades = [
        t for t in trades if t.closed_ts is not None and week_start <= t.closed_ts.date() <= week_end
    ]

    stats_week = analytics.compute_stats(week_trades)
    stats_all = analytics.compute_stats(trades)
    curve = analytics.equity_curve(trades)
    cal = analytics.calendar(trades, week_end.year, week_end.month)
    open_trades = _open_trades(session, mode)
    # ponytail: `_open_crypto_lots` and `_tax_meters` (via `year_summary.summarize`)
    # each replay the live accounts' full transaction history through `run_fifo`
    # independently — two FIFO passes over the same data on every report. Fine at
    # this vault's transaction volume; share one `FifoResult` between them if a
    # report ever gets slow.
    open_lots = _open_crypto_lots(session, week_end)
    tax = _tax_meters(session, week_end.year)

    out_dir = Path(get_settings().DATA_DIR) / "exports" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"weekly-{week_end.isoformat()}-{mode}.pdf"

    with tempfile.TemporaryDirectory() as tmp_dir:
        curve_png = Path(tmp_dir) / "equity.png"
        _plot_equity(curve, week_start, week_end, curve_png)
        pdf = _build_pdf(
            mode=mode,
            week_start=week_start,
            week_end=week_end,
            stats_week=stats_week,
            stats_all=stats_all,
            curve_png=curve_png,
            cal=cal,
            open_trades=open_trades,
            open_lots=open_lots,
            tax=tax,
        )
        pdf.output(str(path))
    return path


def _symbol_map(session: Session, ids: set[int | None]) -> dict[int, str]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return dict(session.execute(select(Instrument.id, Instrument.symbol).where(Instrument.id.in_(ids))).all())


def _open_trades(session: Session, mode: str) -> list[dict]:
    stmt = _trades_in_mode(session, mode, select(Trade).where(Trade.status == TradeStatus.OPEN))
    trades = list(session.execute(stmt).scalars())
    symbols = _symbol_map(session, {t.instrument_id for t in trades})
    return [
        {
            "ref": t.external_ref or f"#{t.id}",
            "symbol": symbols.get(t.instrument_id, "?"),
            "direction": t.direction,
            "entry": t.avg_entry if t.avg_entry is not None else t.planned_entry,
            "stop": t.planned_stop,
            "risk": t.risk_eur,
            "opened": t.opened_ts,
        }
        for t in trades
    ]


def _open_crypto_lots(session: Session, week_end: date) -> list[dict]:
    """Open crypto lots on live accounts.

    # ponytail: `days_to_12m` is the brief's simplified `365 - holding_days`,
    # not `tax.fifo`'s exact calendar-year-aware `over_one_year` (which uses
    # `relativedelta(years=1)` and so is right about leap years). Good enough
    # for a week-to-week glance; switch to `over_one_year` if this figure
    # ever needs to be exact rather than indicative.
    """
    live_ids = accounts_in_mode(session, "live") or []
    if not live_ids:
        return []
    accounts = list(session.execute(select(Account).where(Account.id.in_(live_ids))).scalars())
    transactions = list(
        session.execute(
            select(Transaction).where(Transaction.account_id.in_(live_ids)).order_by(Transaction.ts)
        ).scalars()
    )
    instruments = list(session.execute(select(Instrument)).scalars())
    instrument_by_id = {i.id: i for i in instruments}
    fifo = run_fifo(transactions, instruments, accounts)

    rows = []
    for lot in fifo.open_lots:
        instrument = instrument_by_id.get(lot.instrument_id)
        if instrument is None or instrument.asset_class != AssetClass.CRYPTO or lot.quantity <= 0:
            continue
        holding_days = (week_end - lot.acquired.date()).days
        rows.append(
            {
                "symbol": instrument.symbol,
                "qty": lot.quantity,
                "acquired": lot.acquired.date(),
                "days_to_12m": 365 - holding_days,
            }
        )
    rows.sort(key=lambda r: (r["symbol"], r["acquired"]))
    return rows


def _tax_meters(session: Session, year: int) -> dict:
    """The two Freigrenze/Sparerpauschbetrag meters, for live accounts only —
    tax is about real money (see `api/tax.py`'s `mode=live` default).
    """
    account_ids = accounts_in_mode(session, "live")
    summary = summarize(session, year, account_ids)
    return {
        "p23_used": max(summary.p23.net, Decimal(0)),
        "p23_limit": P23_FREIGRENZE,
        "p20_used": max(summary.p20.dividends + summary.p20.interest, Decimal(0)),
        "p20_limit": SPARERPAUSCHBETRAG,
    }


def _bar(used: Decimal, limit: Decimal, width: int = 20) -> str:
    ratio = min(max(used / limit, Decimal(0)), Decimal(1)) if limit else Decimal(0)
    filled = int(ratio * width)
    return f"[{'#' * filled}{'-' * (width - filled)}] {fmt_pct(ratio)}"


def _plot_equity(curve: list[dict], week_start: date, week_end: date, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 3))
    if curve:
        dates = [p["date"] for p in curve]
        cum_r = [float(p["cum_r"]) for p in curve]
        ax.plot(dates, cum_r, color="#1f77b4", linewidth=1.5)
        ax.axvspan(week_start, week_end, color="#ffd54f", alpha=0.35, label="this week")
        ax.legend(loc="upper left", fontsize=8)
    ax.set_ylabel("Cumulative R")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def _line(pdf: FPDF, text: str, *, size: int = 11, bold: bool = False) -> None:
    pdf.set_font("Helvetica", "B" if bold else "", size)
    pdf.cell(0, _LINE, pdf_safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _section(pdf: FPDF, title: str) -> None:
    pdf.ln(3)
    _line(pdf, title, size=13, bold=True)


def _stats_table(pdf: FPDF, stats_week, stats_all) -> None:
    pdf.set_font("Helvetica", size=10)
    with pdf.table(col_widths=(2, 1, 1), text_align=("LEFT", "RIGHT", "RIGHT")) as table:
        header = table.row()
        for label in ("Metric", "This week", "All-time"):
            header.cell(label)
        for label, get in _STATS_ROWS:
            row = table.row()
            row.cell(label)
            row.cell(pdf_safe(get(stats_week)))
            row.cell(pdf_safe(get(stats_all)))


def _calendar_grid(pdf: FPDF, cal: dict, year: int, month: int) -> None:
    weeks = calendar_mod.Calendar(firstweekday=0).monthdayscalendar(year, month)
    pdf.set_font("Helvetica", size=8)
    with pdf.table(col_widths=(1,) * 7, text_align="CENTER", line_height=4) as table:
        header = table.row()
        for name in analytics.WEEKDAYS:
            header.cell(name)
        for week in weeks:
            row = table.row()
            for day in week:
                if day == 0:
                    row.cell("")
                    continue
                entry = cal.get(date(year, month, day).isoformat())
                text = str(day) if entry is None else f"{day}\n{pdf_safe(fmt_r(entry['r']))}"
                row.cell(text)


def _open_trades_table(pdf: FPDF, open_trades: list[dict]) -> None:
    if not open_trades:
        _line(pdf, "No open trades.", size=10)
        return
    pdf.set_font("Helvetica", size=9)
    with pdf.table(col_widths=(1, 1, 1, 1, 1, 1, 1.4)) as table:
        header = table.row()
        for label in ("Ref", "Symbol", "Direction", "Entry", "Stop", "Risk", "Opened"):
            header.cell(label)
        for t in open_trades:
            row = table.row()
            row.cell(str(t["ref"]))
            row.cell(t["symbol"])
            row.cell(str(t["direction"]))
            row.cell(pdf_safe(fmt_eur(t["entry"])) if t["entry"] is not None else "-")
            row.cell(pdf_safe(fmt_eur(t["stop"])) if t["stop"] is not None else "-")
            row.cell(pdf_safe(fmt_eur(t["risk"])) if t["risk"] is not None else "-")
            row.cell(fmt_date(t["opened"].date()) if t["opened"] is not None else "-")


def _open_lots_table(pdf: FPDF, open_lots: list[dict]) -> None:
    if not open_lots:
        _line(pdf, "No open crypto lots on live accounts.", size=10)
        return
    pdf.set_font("Helvetica", size=9)
    with pdf.table(col_widths=(1, 1, 1, 1)) as table:
        header = table.row()
        for label in ("Symbol", "Qty", "Acquired", "Days to tax-free"):
            header.cell(label)
        for lot in open_lots:
            row = table.row()
            row.cell(lot["symbol"])
            row.cell(fmt_num(lot["qty"], 8).rstrip("0").rstrip(","))
            row.cell(fmt_date(lot["acquired"]))
            days = lot["days_to_12m"]
            row.cell("steuerfrei" if days < 0 else str(days))


def _tax_meters_block(pdf: FPDF, tax: dict) -> None:
    pdf.set_font("Helvetica", size=10)
    _line(pdf, f"Section 23 EStG (Freigrenze {fmt_eur(tax['p23_limit'])}):", size=10)
    _line(pdf, f"  {fmt_eur(tax['p23_used'])}  {_bar(tax['p23_used'], tax['p23_limit'])}", size=10)
    _line(pdf, f"Section 20 EStG Sparerpauschbetrag ({fmt_eur(tax['p20_limit'])}):", size=10)
    _line(pdf, f"  {fmt_eur(tax['p20_used'])}  {_bar(tax['p20_used'], tax['p20_limit'])}", size=10)


def _footer(pdf: FPDF) -> None:
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    _line(pdf, DISCLAIMER, size=8, bold=False)
    _line(pdf, FORM_STATUS, size=8, bold=False)


def _build_pdf(
    *,
    mode: str,
    week_start: date,
    week_end: date,
    stats_week,
    stats_all,
    curve_png: Path,
    cal: dict,
    open_trades: list[dict],
    open_lots: list[dict],
    tax: dict,
) -> FPDF:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    _line(pdf, f"Weekly Report - {mode}", size=16, bold=True)
    _line(pdf, f"Period: {fmt_date(week_start)} to {fmt_date(week_end)}")

    _section(pdf, "Headline stats")
    _stats_table(pdf, stats_week, stats_all)

    _section(pdf, "Equity curve (all-time, cumulative R, this week shaded)")
    pdf.image(str(curve_png), w=180)

    _section(pdf, f"Calendar {calendar_mod.month_name[week_end.month]} {week_end.year}")
    _calendar_grid(pdf, cal, week_end.year, week_end.month)

    _section(pdf, "Open trades")
    _open_trades_table(pdf, open_trades)

    _section(pdf, "Open crypto lots (live accounts)")
    _open_lots_table(pdf, open_lots)

    _section(pdf, f"Tax meters {week_end.year} (live accounts)")
    _tax_meters_block(pdf, tax)

    _footer(pdf)
    return pdf
