"""`trade-ledger tax-year <year>`.

The argparse skeleton in `trade_ledger/cli.py` belongs to another task and is
not on this branch, so the command body lives here as `print_year(session,
year)`; wiring it up is one `subparser` call.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from .anlage import lines as anlage_lines
from .year_summary import YearSummary, accounts_in_mode, summarize

DISCLAIMER = "Berechnung — mit Steuerberater prüfen"
FORM_STATUS = "VZ 2025, geprüft 2026-09-06"


def _eur(value: Decimal) -> str:
    """German money format: 1.234,56 €."""
    return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") + " €"


def _table(header: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(str(cell)) for cell in column) for column in zip(header, *rows, strict=True)]
    out = ["  ".join(h.ljust(w) for h, w in zip(header, widths, strict=True)).rstrip()]
    out.append("  ".join("-" * w for w in widths))
    for row in rows:
        out.append("  ".join(str(c).ljust(w) for c, w in zip(row, widths, strict=True)).rstrip())
    return "\n".join(out)


def render(summary: YearSummary, mode: str = "live") -> str:
    """The whole year as one printable block."""
    p23, p22, p20 = summary.p23, summary.p22, summary.p20
    blocks = [
        f"Steuerjahr {summary.year} (Modus: {mode}) — {DISCLAIMER} (Formstand: {FORM_STATUS})",
        "",
        _table(
            ["Bereich", "Betrag", "Freigrenze", "steuerpflichtig"],
            [
                [
                    "§ 23 Krypto (Gewinn)",
                    _eur(p23.net),
                    _eur(p23.freigrenze),
                    "ja" if p23.exceeded else "nein",
                ],
                [
                    "§ 22 Nr. 3 Leistungen",
                    _eur(p22.net),
                    _eur(p22.freigrenze),
                    "ja" if p22.exceeded else "nein",
                ],
                ["§ 20 ausländische Erträge", _eur(p20.total_foreign), "-", "-"],
                ["§ 23 steuerfrei (> 1 Jahr)", _eur(p23.taxfree_gains), "-", "nein"],
            ],
        ),
    ]

    if summary.inv:
        blocks += [
            "",
            "Anlage KAP-INV",
            _table(
                ["Fonds", "Typ", "TFS %", "Ausschüttung", "Vorabpauschale", "Gewinn", "Verlust"],
                [
                    [
                        row.instrument.symbol,
                        row.fund_type,
                        str(row.teilfreistellung_pct),
                        _eur(row.distributions),
                        _eur(row.vorabpauschale),
                        _eur(row.sale_gain),
                        _eur(row.sale_loss),
                    ]
                    for row in summary.inv
                ],
            ),
        ]

    form_lines = anlage_lines(summary, summary.year)
    if form_lines:
        blocks += [
            "",
            _table(
                ["Formular", "Zeile", "Bezeichnung", "Wert"],
                [
                    [line["form"], str(line["zeile"]), line["label"], line["value"]]
                    for line in form_lines
                ],
            ),
        ]

    if summary.warnings:
        blocks += ["", "Warnungen:"] + [f"  - {w}" for w in summary.warnings]
    return "\n".join(blocks)


def print_year(session: Session, year: int, mode: str = "live") -> None:
    """Print one tax year. `mode` is `live` by default — tax is about real
    money, so paper and demo accounts stay out unless asked for by name.
    """
    print(render(summarize(session, year, accounts_in_mode(session, mode)), mode))
