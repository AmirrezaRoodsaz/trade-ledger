"""Which line of which German form a summary field belongs on.

Line numbers for VZ 2025 (checked 2026-09-06). VERIFY: every number below comes
from secondary sources describing the 2025 forms, not from the official form
PDFs — check them against the paper before filing.

* Anlage SO: the crypto block sits on lines 45-51, the Section 22 Nr. 3
  Leistungen block on lines 14-20.
* Anlage KAP: line 19 total foreign income, 20 share gains contained in it,
  22 losses other than share losses, 23 share losses, 41 creditable foreign
  withholding tax.
* Anlage KAP-INV: 4-8 Ausschuettungen, 9-13 Vorabpauschalen, 14-18
  Veraeusserungsgewinne, 19-23 Veraeusserungsverluste, each block one line per
  fund type in the order aktien, misch, immo, immo_ausland, sonstige.

`key` is resolved by `anlage.lines`: a dotted path into the `YearSummary`,
`inv.<field>.<fund_type>` for a KAP-INV cell, `const:<text>` for fixed text,
`period` for the calendar year, or `<bucket>.flag` for a checkbox.
"""

from __future__ import annotations

from dataclasses import dataclass

CHECKED = "2026-09-06"
VERIFY = "secondary source, VERIFY"


@dataclass(frozen=True)
class Line:
    form: str
    zeile: int
    label: str
    key: str
    checked: str = CHECKED
    note: str = VERIFY


_FUND_ORDER = ["aktien", "misch", "immo", "immo_ausland", "sonstige"]
_FUND_LABELS = {
    "aktien": "Aktienfonds",
    "misch": "Mischfonds",
    "immo": "Immobilienfonds",
    "immo_ausland": "Auslands-Immobilienfonds",
    "sonstige": "sonstige Investmentfonds",
}


def _kap_inv(first_zeile: int, field: str, label: str) -> list[Line]:
    return [
        Line(
            "Anlage KAP-INV", first_zeile + i, f"{label} - {_FUND_LABELS[ft]}", f"inv.{field}.{ft}"
        )
        for i, ft in enumerate(_FUND_ORDER)
    ]


FORMS: dict[int, list[Line]] = {
    2025: [
        Line("Anlage SO", 14, "Sonstige Leistungen (§ 22 Nr. 3 EStG)", "p22.flag"),
        Line("Anlage SO", 15, "Einnahmen", "p22.income"),
        Line("Anlage SO", 19, "Werbungskosten", "p22.werbungskosten"),
        Line("Anlage SO", 20, "Gewinn / Verlust", "p22.net"),
        Line(
            "Anlage SO",
            45,
            "Veraeusserungsgeschaefte mit virtuellen Waehrungen und sonstigen Token",
            "p23.flag",
        ),
        Line("Anlage SO", 46, "Bezeichnung des Wirtschaftsguts", "const:siehe Steuerreport"),
        Line("Anlage SO", 47, "Zeitpunkt der Anschaffung / Veraeusserung", "period"),
        Line("Anlage SO", 48, "Veraeusserungspreis", "p23.proceeds"),
        Line("Anlage SO", 49, "Anschaffungskosten", "p23.cost"),
        Line("Anlage SO", 50, "Werbungskosten", "p23.werbungskosten"),
        Line("Anlage SO", 51, "Gewinn / Verlust", "p23.net"),
        Line("Anlage KAP", 19, "Auslaendische Kapitalertraege", "p20.total_foreign"),
        Line(
            "Anlage KAP",
            20,
            "darin enthaltene Gewinne aus Aktienveraeusserungen",
            "p20.aktien_gains",
        ),
        Line("Anlage KAP", 22, "darin enthaltene Verluste ohne Aktienverluste", "p20.other_losses"),
        Line(
            "Anlage KAP",
            23,
            "darin enthaltene Verluste aus Aktienveraeusserungen",
            "p20.aktien_losses",
        ),
        Line("Anlage KAP", 41, "Anrechenbare auslaendische Steuern", "p20.withholding_tax"),
        *_kap_inv(4, "distributions", "Ausschuettungen"),
        *_kap_inv(9, "vorabpauschale", "Vorabpauschalen"),
        *_kap_inv(14, "sale_gain", "Gewinne aus der Veraeusserung"),
        *_kap_inv(19, "sale_loss", "Verluste aus der Veraeusserung"),
    ]
}
