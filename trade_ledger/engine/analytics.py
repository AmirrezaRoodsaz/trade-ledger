"""Trade performance statistics: expectancy, drawdown, streaks, breakdowns,
the equity curve, the R histogram, the trade calendar and the stage gate.

Pure computation over `list[Trade]` — ORM rows or duck-typed objects with the
same attributes (`status`, `risk_eur`, `result_eur`, `r_multiple`, `mae_r`,
`mfe_r`, `opened_ts`, `closed_ts`, `adherence`, `mistake`, `tags`,
`emotion_post`). No session, no query, no import from `..db` or `..models`.

`breakdown`'s `playbook`/`instrument`/`account` keys need names the trade row
itself doesn't carry (only the foreign-key ids do) — the caller is expected
to have attached `playbook_label`, `instrument_symbol` and `account_name` to
each trade before calling; a trade without one groups under "none"/"unknown".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from ..enums import TradeStatus

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass
class Stats:
    count: int
    wins: int
    losses: int
    flat: int
    win_rate: Decimal
    expectancy_r: Decimal
    total_r: Decimal
    total_eur: Decimal
    avg_win_r: Decimal
    avg_loss_r: Decimal
    payoff: Decimal | None
    profit_factor: Decimal | None
    sqn: Decimal | None
    std_r: Decimal | None
    max_dd_r: Decimal
    max_dd_eur: Decimal
    recovery_factor: Decimal | None
    max_win_streak: int
    max_loss_streak: int
    avg_hold_hours_win: Decimal | None
    avg_hold_hours_loss: Decimal | None
    adherence: Decimal | None
    cost_of_mistakes_r: Decimal
    cost_of_mistakes_eur: Decimal
    mistakes: dict[str, int]
    avg_mae_r: Decimal | None
    avg_mfe_r: Decimal | None
    capture: Decimal | None


def closed(trades) -> list:
    """Closed trades with a real risk and result, oldest first."""
    rows = [
        t
        for t in trades
        if t.status == TradeStatus.CLOSED and (t.risk_eur or Decimal(0)) > 0 and t.result_eur is not None
    ]
    rows.sort(key=lambda t: t.closed_ts)
    return rows


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / len(values)


def _empty_stats() -> Stats:
    return Stats(
        count=0,
        wins=0,
        losses=0,
        flat=0,
        win_rate=Decimal(0),
        expectancy_r=Decimal(0),
        total_r=Decimal(0),
        total_eur=Decimal(0),
        avg_win_r=Decimal(0),
        avg_loss_r=Decimal(0),
        payoff=None,
        profit_factor=None,
        sqn=None,
        std_r=None,
        max_dd_r=Decimal(0),
        max_dd_eur=Decimal(0),
        recovery_factor=None,
        max_win_streak=0,
        max_loss_streak=0,
        avg_hold_hours_win=None,
        avg_hold_hours_loss=None,
        adherence=None,
        cost_of_mistakes_r=Decimal(0),
        cost_of_mistakes_eur=Decimal(0),
        mistakes={},
        avg_mae_r=None,
        avg_mfe_r=None,
        capture=None,
    )


def compute_stats(trades) -> Stats:
    trades = closed(trades)
    n = len(trades)
    if n == 0:
        return _empty_stats()

    r_values = [t.r_multiple for t in trades]
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    flat = [r for r in r_values if r == 0]

    total_r = sum(r_values, Decimal(0))
    total_eur = sum((t.result_eur for t in trades), Decimal(0))
    expectancy_r = total_r / n
    win_rate = Decimal(len(wins)) / n
    avg_win_r = _mean(wins) if wins else Decimal(0)
    avg_loss_r = _mean(losses) if losses else Decimal(0)
    payoff = (avg_win_r / abs(avg_loss_r)) if losses else None

    pos_sum = sum(wins, Decimal(0))
    neg_sum = sum(losses, Decimal(0))
    profit_factor = (pos_sum / abs(neg_sum)) if neg_sum != 0 else None

    if n >= 2:
        variance = sum(((r - expectancy_r) ** 2 for r in r_values), Decimal(0)) / (n - 1)
        std_r = variance.sqrt()
        sqn = (expectancy_r / std_r) * Decimal(n).sqrt() if std_r != 0 else None
    else:
        std_r = None
        sqn = None

    max_dd_r, max_dd_eur, max_win_streak, max_loss_streak = _drawdown_and_streaks(trades)
    recovery_factor = (total_r / max_dd_r) if max_dd_r > 0 else None

    avg_hold_hours_win = _avg_hold_hours(trades, want_win=True)
    avg_hold_hours_loss = _avg_hold_hours(trades, want_win=False)

    reviewed = [t for t in trades if t.adherence is not None]
    adherence = (
        Decimal(sum(1 for t in reviewed if t.adherence is True)) / len(reviewed) if reviewed else None
    )

    mistaken = [t for t in trades if t.mistake]
    cost_of_mistakes_r = sum((t.r_multiple for t in mistaken), Decimal(0))
    cost_of_mistakes_eur = sum((t.result_eur for t in mistaken), Decimal(0))
    mistakes: dict[str, int] = {}
    for t in mistaken:
        mistakes[t.mistake] = mistakes.get(t.mistake, 0) + 1

    maes = [t.mae_r for t in trades if t.mae_r is not None]
    mfes = [t.mfe_r for t in trades if t.mfe_r is not None]
    avg_mae_r = _mean(maes) if maes else None
    avg_mfe_r = _mean(mfes) if mfes else None

    captures = [t.r_multiple / t.mfe_r for t in trades if t.mfe_r is not None and t.mfe_r > 0]
    capture = _mean(captures) if captures else None

    return Stats(
        count=n,
        wins=len(wins),
        losses=len(losses),
        flat=len(flat),
        win_rate=win_rate,
        expectancy_r=expectancy_r,
        total_r=total_r,
        total_eur=total_eur,
        avg_win_r=avg_win_r,
        avg_loss_r=avg_loss_r,
        payoff=payoff,
        profit_factor=profit_factor,
        sqn=sqn,
        std_r=std_r,
        max_dd_r=max_dd_r,
        max_dd_eur=max_dd_eur,
        recovery_factor=recovery_factor,
        max_win_streak=max_win_streak,
        max_loss_streak=max_loss_streak,
        avg_hold_hours_win=avg_hold_hours_win,
        avg_hold_hours_loss=avg_hold_hours_loss,
        adherence=adherence,
        cost_of_mistakes_r=cost_of_mistakes_r,
        cost_of_mistakes_eur=cost_of_mistakes_eur,
        mistakes=mistakes,
        avg_mae_r=avg_mae_r,
        avg_mfe_r=avg_mfe_r,
        capture=capture,
    )


def _drawdown_and_streaks(trades: list) -> tuple[Decimal, Decimal, int, int]:
    """One pass over closed-order trades: peak-to-trough drawdown (in R and
    EUR) and the longest run of wins/losses. A flat trade (r == 0) breaks
    both streaks.
    """
    cum_r = peak_r = max_dd_r = Decimal(0)
    cum_eur = peak_eur = max_dd_eur = Decimal(0)
    cur_win = cur_loss = max_win_streak = max_loss_streak = 0
    for t in trades:
        r = t.r_multiple
        cum_r += r
        peak_r = max(peak_r, cum_r)
        max_dd_r = max(max_dd_r, peak_r - cum_r)

        cum_eur += t.result_eur
        peak_eur = max(peak_eur, cum_eur)
        max_dd_eur = max(max_dd_eur, peak_eur - cum_eur)

        if r > 0:
            cur_win, cur_loss = cur_win + 1, 0
        elif r < 0:
            cur_win, cur_loss = 0, cur_loss + 1
        else:
            cur_win = cur_loss = 0
        max_win_streak = max(max_win_streak, cur_win)
        max_loss_streak = max(max_loss_streak, cur_loss)
    return max_dd_r, max_dd_eur, max_win_streak, max_loss_streak


def _avg_hold_hours(trades: list, *, want_win: bool) -> Decimal | None:
    hours = []
    for t in trades:
        if t.opened_ts is None:
            continue
        r = t.r_multiple
        if want_win and r <= 0:
            continue
        if not want_win and r >= 0:
            continue
        hours.append(Decimal((t.closed_ts - t.opened_ts).total_seconds()) / Decimal(3600))
    return _mean(hours) if hours else None


def _tags(trade) -> list[str]:
    raw = getattr(trade, "tags", None)
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            return json.loads(raw) or []
        except (TypeError, ValueError):
            return []
    return list(raw)


def _hold_bucket(trade) -> str:
    if trade.opened_ts is None:
        return ">4w"
    days = (trade.closed_ts - trade.opened_ts).total_seconds() / 86400
    if days < 1:
        return "<1d"
    if days < 3:
        return "1–3d"
    if days < 7:
        return "3–7d"
    if days < 28:
        return "1–4w"
    return ">4w"


_KEY_EXTRACTORS = {
    "playbook": lambda t: getattr(t, "playbook_label", None) or "none",
    "instrument": lambda t: getattr(t, "instrument_symbol", None) or "unknown",
    "account": lambda t: getattr(t, "account_name", None) or "unknown",
    "weekday": lambda t: WEEKDAYS[t.closed_ts.weekday()],
    "month": lambda t: t.closed_ts.strftime("%Y-%m"),
    "hold_bucket": _hold_bucket,
    "emotion": lambda t: t.emotion_post or "none",
}


def breakdown(trades, key: str) -> dict[str, Stats]:
    """Group closed trades by `key` and compute `Stats` per group. `tag` is
    the one multi-membership key — a trade with three tags counts in all
    three groups.
    """
    trades = closed(trades)
    groups: dict[str, list] = {}
    if key == "tag":
        for t in trades:
            for tag in _tags(t):
                groups.setdefault(tag, []).append(t)
    else:
        extractor = _KEY_EXTRACTORS.get(key)
        if extractor is None:
            raise ValueError(f"unknown breakdown key: {key}")
        for t in trades:
            groups.setdefault(extractor(t), []).append(t)
    return {name: compute_stats(group) for name, group in groups.items()}


def equity_curve(trades) -> list[dict]:
    trades = closed(trades)
    cum_r = peak_r = Decimal(0)
    cum_eur = peak_eur = Decimal(0)
    out = []
    for t in trades:
        cum_r += t.r_multiple
        peak_r = max(peak_r, cum_r)
        cum_eur += t.result_eur
        peak_eur = max(peak_eur, cum_eur)
        out.append(
            {
                "date": t.closed_ts.date(),
                "cum_r": cum_r,
                "cum_eur": cum_eur,
                "dd_r": peak_r - cum_r,
                "dd_eur": peak_eur - cum_eur,
            }
        )
    return out


def r_histogram(trades, bin: Decimal = Decimal("0.5")) -> list[dict]:
    """Non-empty bins of width `bin`, sorted ascending. A trade's bin is the
    half-open interval `[lo, lo + bin)` its R multiple falls into.
    """
    trades = closed(trades)
    counts: dict[Decimal, int] = {}
    for t in trades:
        idx = (t.r_multiple / bin).to_integral_value(rounding=ROUND_FLOOR)
        counts[idx] = counts.get(idx, 0) + 1
    out = []
    for idx in sorted(counts):
        lo = idx * bin
        out.append({"lo": lo, "hi": lo + bin, "count": counts[idx]})
    return out


def calendar(trades, year: int, month: int) -> dict[str, dict]:
    """Closed trades of one month, keyed by ISO close date."""
    trades = closed(trades)
    out: dict[str, dict] = {}
    for t in trades:
        d = t.closed_ts.date()
        if d.year != year or d.month != month:
            continue
        entry = out.setdefault(d.isoformat(), {"count": 0, "r": Decimal(0), "eur": Decimal(0)})
        entry["count"] += 1
        entry["r"] += t.r_multiple
        entry["eur"] += t.result_eur
    return out


_MIN_TRADES = {"paper": 30, "live": 50}
_MIN_ADHERENCE = Decimal("0.90")
_MAX_DRAWDOWN_RATIO = Decimal("0.20")
_NEXT = {
    "paper": "Stage 1 — first live capital.",
    "live": "Stage 2 — increase live sizing.",
}


def stage_gate(trades, mode: str, capital: Decimal) -> dict:
    """Same checks as the money side's `trade_stats.py`: minimum closed
    trades (30 paper / 50 live), positive expectancy, adherence >= 90 %, and
    max drawdown (EUR) within 20 % of `capital`.
    """
    if mode not in _MIN_TRADES:
        raise ValueError(f"stage-gate requires mode 'paper' or 'live', got {mode!r}")

    stats = compute_stats(trades)
    min_trades = _MIN_TRADES[mode]
    dd_ratio = (stats.max_dd_eur / capital) if capital > 0 else None
    adherence_actual = stats.adherence if stats.adherence is not None else Decimal(0)

    checks = [
        {
            "name": f"at least {min_trades} closed trades",
            "passed": stats.count >= min_trades,
            "actual": stats.count,
            "required": min_trades,
        },
        {
            "name": "expectancy above zero",
            "passed": stats.expectancy_r > 0,
            "actual": stats.expectancy_r,
            "required": Decimal(0),
        },
        {
            "name": f"adherence at least {_MIN_ADHERENCE:.0%}",
            "passed": stats.adherence is not None and stats.adherence >= _MIN_ADHERENCE,
            "actual": adherence_actual,
            "required": _MIN_ADHERENCE,
        },
        {
            "name": f"max drawdown within {_MAX_DRAWDOWN_RATIO:.0%} of capital",
            "passed": dd_ratio is not None and dd_ratio <= _MAX_DRAWDOWN_RATIO,
            "actual": dd_ratio if dd_ratio is not None else Decimal(0),
            "required": _MAX_DRAWDOWN_RATIO,
        },
    ]
    return {
        "mode": mode,
        "capital": capital,
        "checks": checks,
        "passed": all(c["passed"] for c in checks),
        "next": _NEXT[mode],
    }
