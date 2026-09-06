"""Render a `YearSummary` onto the form lines of `forms.FORMS`."""

from __future__ import annotations

from decimal import Decimal

from .forms import FORMS
from .year_summary import YearSummary


def _resolve(summary: YearSummary, key: str, vz: int):
    if key.startswith("const:"):
        return key.removeprefix("const:")
    if key == "period":
        return f"01.01.{vz} - 31.12.{vz}"
    if key.startswith("inv."):
        _, field, fund_type = key.split(".")
        return sum(
            (getattr(row, field) for row in summary.inv if row.fund_type == fund_type),
            Decimal(0),
        )
    bucket, _, field = key.partition(".")
    value = getattr(summary, bucket)
    if field == "flag":
        # A checkbox: crossed when the block has anything to report at all.
        rows = getattr(value, "disposals", None)
        if rows is None:
            rows = getattr(value, "items", [])
        return "X" if rows else ""
    return getattr(value, field)


def lines(summary: YearSummary, vz: int) -> list[dict]:
    """Form lines for `vz`, each `{form, zeile, label, value, note}`.

    Money is formatted as a plain decimal string (`"1200.00"`), matching the
    API's money-as-string convention. An empty form year yields an empty list.
    """
    out = []
    for line in FORMS.get(vz, []):
        value = _resolve(summary, line.key, vz)
        out.append(
            {
                "form": line.form,
                "zeile": line.zeile,
                "label": line.label,
                "value": f"{value:.2f}" if isinstance(value, Decimal) else value,
                "note": line.note,
            }
        )
    return out
