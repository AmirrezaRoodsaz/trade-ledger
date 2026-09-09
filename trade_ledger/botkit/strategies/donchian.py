"""Donchian channel breakout, long only, on completed bars.

Entry: the last completed close breaks above the highest high of the
`entry` bars *before* it. Stop: that close minus `atr_mult` x Wilder
ATR(`atr_len`). Exit: the close breaks below the lowest low of the `exit`
bars before it.

The current bar is excluded from both channels — a bar can hardly break out
of a range it defines. `Exchange.candles` only ever returns closed bars, so
"the last completed bar" is simply `bars[-1]`.

Decimal throughout: these numbers become an order size and a stop price.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ...prices.service import Candle


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: str  # "buy" = entry, "sell" = exit
    entry: Decimal | None
    stop: Decimal | None
    reason: str


def default_params() -> dict:
    return {"entry": 55, "exit": 20, "atr_len": 20, "atr_mult": 2}


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def true_range(bar: Candle, prev_close: Decimal) -> Decimal:
    return max(
        bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close)
    )


def atr(bars: list[Candle], length: int) -> Decimal | None:
    """Wilder ATR over the true ranges of `bars` (the first bar only seeds
    the previous close). `None` when there are fewer than `length` true
    ranges to average.
    """
    ranges = [true_range(bar, bars[i].close) for i, bar in enumerate(bars[1:])]
    if len(ranges) < length:
        return None
    value = sum(ranges[:length], Decimal(0)) / length
    for tr in ranges[length:]:
        value = (value * (length - 1) + tr) / length
    return value


def _usable(bars: list[Candle]) -> list[Candle]:
    return [b for b in bars if None not in (b.open, b.high, b.low, b.close)]


def signals(
    candles: dict[str, list[Candle]], params: dict, open_positions: dict[str, dict]
) -> list[Signal]:
    """One signal per symbol at most: an exit when the symbol is held, an
    entry when it is not. Symbols with too little history are skipped.
    """
    p = default_params() | params
    entry_len, exit_len = int(p["entry"]), int(p["exit"])
    atr_len, atr_mult = int(p["atr_len"]), _dec(p["atr_mult"])

    out: list[Signal] = []
    for symbol, raw in sorted(candles.items()):
        bars = _usable(raw)
        held = symbol in open_positions
        window = exit_len if held else entry_len
        if len(bars) < max(window, atr_len) + 1:
            continue

        last = bars[-1]
        prior = bars[-1 - window : -1]

        if held:
            floor = min(b.low for b in prior)
            if last.close < floor:
                out.append(
                    Signal(
                        symbol=symbol,
                        side="sell",
                        entry=None,
                        stop=None,
                        reason=f"close {last.close} below {exit_len}-bar low {floor}",
                    )
                )
            continue

        ceiling = max(b.high for b in prior)
        if last.close <= ceiling:
            continue
        band = atr(bars, atr_len)
        if band is None:
            continue
        out.append(
            Signal(
                symbol=symbol,
                side="buy",
                entry=last.close,
                stop=last.close - atr_mult * band,
                reason=(
                    f"close {last.close} above {entry_len}-bar high {ceiling}, "
                    f"ATR({atr_len}) {band}"
                ),
            )
        )
    return out
