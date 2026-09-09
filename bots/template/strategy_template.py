"""Strategy template — copy, rename, fill in the two rules in `signals`.

`trade-bot new <name>` copies this file to `bots/strategies/<name>.py` and
writes `data/bots/<name>/.env`. Move the finished module into
`trade_ledger/botkit/strategies/` and register it there (see the bottom of
this file): that package is what the runner imports, and the imports below
become relative (`from ...prices.service import Candle`).

The contract this implements is `bots/BOT_CONTRACT.md`, section 6.

**Inputs**

| Argument | Shape | Meaning |
|---|---|---|
| `candles` | `{symbol: [Candle, …]}` | oldest first, **completed bars only** — the forming bar is never in here |
| `Candle.date` | aware `datetime` | when the bar closed, UTC |
| `Candle.open/high/low/close` | `Decimal \\| None` | `None` on a gap; skip those bars |
| `params` | `dict` | the preset's parameters, merged over `default_params()` |
| `open_positions` | `{symbol: {"qty": Decimal}}` | what the journal says is held; a missing symbol is flat |

**Output** — a `list[Signal]`, at most one per symbol:

| Field | Shape | Meaning |
|---|---|---|
| `symbol` | `str` | a key of `candles` |
| `side` | `"buy"` / `"sell"` | `buy` opens a position, `sell` closes the held one |
| `entry` | `Decimal \\| None` | entry price for a buy; `None` for a sell |
| `stop` | `Decimal \\| None` | stop price for a buy (mandatory, below the entry); `None` for a sell |
| `reason` | `str` | the sentence that lands in the journal and the run summary |

Run it on its own for the self-check:

    uv run python bots/template/strategy_template.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_ledger.botkit.strategies import Signal
from trade_ledger.prices.service import Candle


def default_params() -> dict:
    """Every parameter the strategy reads, with a sane value.

    The preset's `params` are merged **over** these, so a preset that sets
    nothing still runs. Keep them JSON types (int, float, str, bool) — they
    travel through the app as JSON.
    """
    return {"lookback": 20, "threshold_pct": 1}


def signals(
    candles: dict[str, list[Candle]],
    params: dict,
    open_positions: dict[str, dict],
) -> list[Signal]:
    """Decide what to do, from bars alone. See the module docstring for the
    shape of every argument and of the returned signals.

    Skip a symbol with too little history rather than guessing; an empty list
    is a perfectly good answer. Exit signals may only name a symbol in
    `open_positions`, entry signals only a symbol that is not in it.

    **Determinism**: the same candles, params and positions must give the same
    signals. No clock, no network, no randomness, no file — that is what makes
    a backtest comparable with the journal, and a re-run of the same bar safe.

    Use `Decimal` for every price: these numbers become an order and a stop.
    """
    p = default_params() | params
    lookback = int(p["lookback"])
    threshold = Decimal(str(p["threshold_pct"])) / 100

    out: list[Signal] = []
    for symbol, raw in sorted(candles.items()):
        bars = [b for b in raw if None not in (b.open, b.high, b.low, b.close)]
        if len(bars) < lookback + 1:
            continue

        last = bars[-1]
        prior = bars[-1 - lookback : -1]
        average = sum((b.close for b in prior), Decimal(0)) / len(prior)

        if symbol in open_positions:
            # --- your exit rule here ---
            if last.close < average:
                out.append(
                    Signal(
                        symbol=symbol,
                        side="sell",
                        entry=None,
                        stop=None,
                        reason=f"close {last.close} below the {lookback}-bar mean {average}",
                    )
                )
            continue

        # --- your entry rule here ---
        if last.close > average * (1 + threshold):
            out.append(
                Signal(
                    symbol=symbol,
                    side="buy",
                    entry=last.close,
                    # A stop is mandatory: the runner refuses an entry whose
                    # stop equals the entry, and never holds a position
                    # without one.
                    stop=average,
                    reason=(
                        f"close {last.close} more than {p['threshold_pct']}% above "
                        f"the {lookback}-bar mean {average}"
                    ),
                )
            )
    return out


def demo() -> None:
    """Self-check: 60 synthetic bars in, a list of `Signal` out."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rising = [
        Candle(
            date=start + timedelta(hours=4 * i),
            open=Decimal(100 + i),
            high=Decimal(101 + i),
            low=Decimal(99 + i),
            close=Decimal(100 + i),
        )
        for i in range(60)
    ]
    falling = [
        Candle(date=c.date, open=c.open, high=c.high, low=c.low, close=Decimal(200) - c.close)
        for c in rising
    ]

    entries = signals({"UP/EUR": rising}, {}, {})
    assert isinstance(entries, list), entries
    assert all(isinstance(s, Signal) for s in entries), entries
    assert [(s.symbol, s.side) for s in entries] == [("UP/EUR", "buy")], entries
    assert entries[0].entry is not None and entries[0].stop is not None
    assert entries[0].stop < entries[0].entry, "a long stop sits below the entry"

    exits = signals({"UP/EUR": falling}, {}, {"UP/EUR": {"qty": Decimal(1)}})
    assert [(s.symbol, s.side) for s in exits] == [("UP/EUR", "sell")], exits
    assert exits[0].entry is None and exits[0].stop is None

    # Determinism, and too little history is silence rather than a guess.
    assert signals({"UP/EUR": rising}, {}, {}) == entries
    assert signals({"UP/EUR": rising[:5]}, {}, {}) == []

    print(f"ok: {len(entries)} entry signal(s), {len(exits)} exit signal(s)")


if __name__ == "__main__":
    demo()

# Register the finished strategy in
# `trade_ledger/botkit/strategies/__init__.py`:
#
#     from . import my_strategy
#     STRATEGIES = {..., "my_strategy": my_strategy.signals}
#     DEFAULT_PARAMS = {..., "my_strategy": my_strategy.default_params()}
#
# The preset's `strategy` field is that key.
