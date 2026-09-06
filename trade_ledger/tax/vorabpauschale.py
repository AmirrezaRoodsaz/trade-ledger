"""Vorabpauschale for fund and ETF lots still held on 31 December.

Legal background (checked 2026-09-06): Section 18 InvStG. The Basisertrag is
70 % of the Basiszins on the value of the fund at the beginning of the year;
the Vorabpauschale is that Basisertrag capped at the actual gain in value over
the year and reduced by the distributions already received. For a fund bought
during the year the Basisertrag is cut by one twelfth for every month before
the month of acquisition (Section 18 Abs. 2 InvStG).

Basiszins is published by the BMF at the start of each year:

| year | Basiszins | source |
|------|-----------|--------|
| 2024 | 2,29 %    | BMF    |
| 2025 | 2,53 %    | BMF letter of 10.01.2025 |
| 2026 | 3,20 %    | BMF letter of 13.01.2026 |

VERIFY: these rates and letter dates come from secondary sources, not from the
letters themselves. Confirm before relying on them. A `Setting` row
`basiszins_<year>` overrides the table.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from ..models import Setting

CENT = Decimal("0.01")
BASISZINS = {2024: Decimal("0.0229"), 2025: Decimal("0.0253"), 2026: Decimal("0.0320")}


def basiszins_for(session: Session, year: int) -> Decimal | None:
    """Basiszins for `year`: the `basiszins_<year>` setting, else `BASISZINS`."""
    override = session.get(Setting, f"basiszins_{year}")
    if override is not None:
        return Decimal(override.value)
    return BASISZINS.get(year)


def months_factor(acquisition_month: int | None) -> Decimal:
    """`(12 − month + 1)/12` for a lot bought during the year, `1` otherwise."""
    if acquisition_month is None:
        return Decimal(1)
    return Decimal(12 - acquisition_month + 1) / 12


def compute(
    lot_qty: Decimal,
    price_jan1: Decimal,
    price_dec31: Decimal,
    distributions_eur: Decimal,
    basiszins: Decimal,
    months_factor: Decimal,
) -> Decimal:
    """Vorabpauschale in EUR for one lot. Never negative.

    10 shares at 100 € on 1 January, 110 € on 31 December, Basiszins 2,53 %,
    no distributions, held the whole year: `min(17,71; 100,00) = 17,71 €`.
    """
    basisertrag = price_jan1 * lot_qty * basiszins * Decimal("0.7") * months_factor
    wertzuwachs = max(Decimal(0), (price_dec31 - price_jan1) * lot_qty)
    value = max(Decimal(0), min(basisertrag, wertzuwachs) - distributions_eur)
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
