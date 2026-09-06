"""Maximum adverse/favourable excursion from daily candles.

Daily resolution only: a trade's whole life is reduced to one low and one
high per day, so an intraday spike that reverses before the close is missed.
Good enough for a swing-trade journal; upgrade to intraday candles if scalps
ever need grading.
"""

from __future__ import annotations

from decimal import Decimal

from ..enums import Direction
from ..models import Trade
from ..prices.service import Candle


def compute_excursions(trade: Trade, candles: list[Candle]) -> tuple[Decimal, Decimal] | None:
    """`(mae_eur, mfe_eur)` for `trade` over `candles`.

    Long: `mae = (min(low) - avg_entry) * qty` (<= 0 while the trade dipped
    below entry), `mfe = (max(high) - avg_entry) * qty` (>= 0). Short mirrors
    it: a rally above entry is the adverse move, a drop below it the
    favourable one. Returns `None` when `candles` is empty (or carries no
    low/high at all) rather than a false zero, so the caller can skip storing
    a result.
    """
    lows = [c.low for c in candles if c.low is not None]
    highs = [c.high for c in candles if c.high is not None]
    if not lows or not highs:
        return None

    low, high = min(lows), max(highs)
    entry, qty = trade.avg_entry, trade.quantity
    if trade.direction == Direction.LONG:
        return (low - entry) * qty, (high - entry) * qty
    return (entry - high) * qty, (entry - low) * qty


def _demo() -> None:
    from datetime import date

    class _T:
        direction = Direction.LONG
        avg_entry = Decimal(100)
        quantity = Decimal(2)

    def c(low, high):
        return Candle(
            date=date(2026, 1, 1), open=None, high=Decimal(high), low=Decimal(low), close=None
        )

    long_trade = _T()
    assert compute_excursions(long_trade, [c(90, 105), c(95, 130)]) == (Decimal(-20), Decimal(60))

    short_trade = _T()
    short_trade.direction = Direction.SHORT
    assert compute_excursions(short_trade, [c(70, 105), c(95, 110)]) == (Decimal(-20), Decimal(60))

    assert compute_excursions(long_trade, []) is None


if __name__ == "__main__":
    _demo()
    print("ok")
