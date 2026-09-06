"""de-DE display formatting for reports. Money stays `Decimal` everywhere
else in the app; these functions are the last step, turning a `Decimal` or
`date` into the string a human reads (`1.234,56 €`, `06.09.2026`).

`pdf_safe` is separate: fpdf2's bundled core fonts (Helvetica) only encode
latin-1/cp1252, and this repo bundles no TTF font to add Unicode support, so
the euro sign and the em dash — both outside that range — are swapped for
ASCII before any text reaches a PDF. JSON responses use the formatters above
directly and keep the real characters.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal


def _de(value: Decimal, decimals: int) -> str:
    """`Decimal` -> de-DE formatted string with a fixed number of decimals
    (`,` for the decimal point, `.` for thousands), rounded half-up.
    """
    quant = Decimal(1).scaleb(-decimals)
    value = value.quantize(quant, rounding=ROUND_HALF_UP)
    text = f"{value:,.{decimals}f}"
    return text.translate(str.maketrans({",": "", ".": ","})).replace("", ".")


def fmt_eur(value: Decimal) -> str:
    return f"{_de(value, 2)} €"


def fmt_r(value: Decimal) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{_de(value, 2)} R"


def fmt_pct(value: Decimal) -> str:
    """`value` is a fraction (`Decimal("0.55")` -> `"55,0 %"`)."""
    return f"{_de(value * 100, 1)} %"


def fmt_num(value: Decimal, decimals: int = 2) -> str:
    """Plain de-DE number, no unit — profit factor, SQN and the like."""
    return _de(value, decimals)


def fmt_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def pdf_safe(text: str) -> str:
    """Swap the glyphs fpdf2's latin-1 core fonts can't render."""
    return text.replace("€", "EUR").replace("—", "-")
