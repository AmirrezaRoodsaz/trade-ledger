"""K1–K5: the five rules that decide whether a bot is still allowed to run.

Pure functions over rows that were handed in — no session, no query, no
alerting, no command queueing. `monitor.py` owns the consequences.

Every rule returns the same dict::

    {"rule": "K1", "status": "ok|warning|triggered", "value": "...",
     "threshold": "...", "action": "pause|alert|flat|none", "detail": "..."}

`status` carries the rule's own severity: K2 and K3 say `warning` when they
fire (spec: "warning alert only"), the other three say `triggered`. `value`
and `threshold` are strings so a Decimal never becomes a float on the way to
the UI; both are `None` when the rule had nothing to measure.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from decimal import Decimal

from ..engine import analytics
from ..models import Bot, BotRun, BotState
from .schedule import deadline, next_run

# Thresholds, from the plan's Global Constraints.
EQUITY_FLOOR_PCT = Decimal(80)  # K1: equity as a percentage of stage capital
MAX_DRAWDOWN_PCT = Decimal(20)  # K1: from peak equity
EDGE_WINDOW = 20  # K2: number of closed trades
MIN_PROFIT_FACTOR = Decimal(1)  # K2
MAX_LOSS_STREAK = 8  # K3: consecutive losses
MAX_MISSED_RUNS = 2  # K4


def _pct(value: Decimal) -> str:
    """A percentage as a plain string: `76`, `24.5`, `0.33`."""
    text = format(value.quantize(Decimal("0.01")), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _result(rule: str, status: str, value, threshold, action: str, detail: str) -> dict:
    return {
        "rule": rule,
        "status": status,
        "value": value,
        "threshold": threshold,
        "action": action,
        "detail": detail,
    }


def positions(state: BotState | None) -> list[dict]:
    """The bot's last reported positions, or an empty list."""
    if state is None:
        return []
    parsed = json.loads(state.positions_json or "[]")
    return parsed if isinstance(parsed, list) else []


def drawdown_pct(state: BotState | None) -> Decimal | None:
    """Percent below the peak equity the bot has ever reported.

    Quantised to two decimals: the division is exact but can run to 28
    significant digits, and nothing reads a drawdown that finely.
    """
    if state is None or state.equity_eur is None or not state.peak_equity_eur:
        return None
    fall = (state.peak_equity_eur - state.equity_eur) / state.peak_equity_eur * 100
    return fall.quantize(Decimal("0.01"))


def missed_runs(bot: Bot, last_run: BotRun | None, now: datetime) -> int:
    """Schedule slots that came and went since the last run started.

    A slot only counts as missed once `grace_s` past it has gone by, the same
    allowance K5 gives a heartbeat — a bot that starts a minute late has not
    missed anything.

    Zero for a bot that has never run — K5 is the rule that notices a bot
    which never showed up at all.
    """
    if last_run is None:
        return 0
    first = next_run(bot.schedule_every_s, bot.schedule_at, last_run.started)
    late = (now - first).total_seconds() - bot.grace_s
    if late < 0:
        return 0
    return math.floor(late / bot.schedule_every_s) + 1


def k1_capital(state: BotState | None, stage_capital: Decimal) -> dict:
    """Equity at or below 80 % of stage capital, or 20 % off the peak."""
    threshold = f"{_pct(EQUITY_FLOOR_PCT)}% of capital / {_pct(MAX_DRAWDOWN_PCT)}% drawdown"
    if state is None or state.equity_eur is None:
        if positions(state):
            # The bot reports equity only when every pair settles in EUR (FX
            # conversion lives in the app). A bot on a USDT account therefore
            # has money at the venue and a capital brake that can never fire —
            # silence there reads as "fine", so say so out loud instead.
            return _result(
                "K1",
                "warning",
                None,
                threshold,
                "alert",
                "no equity in EUR reported; K1 cannot evaluate "
                "(use an EUR-quoted account)",
            )
        return _result("K1", "ok", None, threshold, "none", "no equity reported yet")
    if not stage_capital:
        return _result("K1", "ok", None, threshold, "none", "stage capital is zero")

    equity_pct = state.equity_eur / stage_capital * 100
    drawdown = drawdown_pct(state) or Decimal(0)
    detail = (
        f"equity {state.equity_eur} = {_pct(equity_pct)}% of stage capital "
        f"{stage_capital}, {_pct(drawdown)}% below peak"
    )
    if equity_pct <= EQUITY_FLOOR_PCT:
        return _result("K1", "triggered", _pct(equity_pct), _pct(EQUITY_FLOOR_PCT), "pause", detail)
    if drawdown >= MAX_DRAWDOWN_PCT:
        return _result("K1", "triggered", _pct(drawdown), _pct(MAX_DRAWDOWN_PCT), "pause", detail)
    return _result("K1", "ok", _pct(equity_pct), _pct(EQUITY_FLOOR_PCT), "none", detail)


def k2_edge(trades: list) -> dict:
    """Profit factor below 1,0 over the last 20 closed trades."""
    threshold = _pct(MIN_PROFIT_FACTOR)
    if len(trades) < EDGE_WINDOW:
        return _result(
            "K2",
            "ok",
            None,
            threshold,
            "none",
            f"{len(trades)} of {EDGE_WINDOW} closed trades so far",
        )
    factor = analytics.compute_stats(trades[-EDGE_WINDOW:]).profit_factor
    if factor is None:
        # No losing trade in the window: there is nothing to divide by, and a
        # bot that never loses is not the one this rule is looking for.
        return _result("K2", "ok", None, threshold, "none", "no losses in the window")
    detail = f"profit factor {_pct(factor)} over the last {EDGE_WINDOW} closed trades"
    status = "warning" if factor < MIN_PROFIT_FACTOR else "ok"
    return _result(
        "K2", status, _pct(factor), threshold, "alert" if status != "ok" else "none", detail
    )


def k3_streak(trades: list) -> dict:
    """Eight losses in a row, counted back from the most recent trade."""
    streak = 0
    for trade in reversed(trades):
        if (trade.result_eur or Decimal(0)) >= 0:
            break
        streak += 1
    detail = f"{streak} consecutive losses"
    status = "warning" if streak >= MAX_LOSS_STREAK else "ok"
    return _result(
        "K3",
        status,
        str(streak),
        str(MAX_LOSS_STREAK),
        "alert" if status != "ok" else "none",
        detail,
    )


def k4_integrity(bot: Bot, state: BotState | None, last_run: BotRun | None, now: datetime) -> dict:
    """Reconciliation mismatch, a position without a stop, or two missed runs.

    A stop-less position is the one case the app answers with `flat` — the
    others it can only shout about, because closing on a mismatch is the
    bot's own job (it is the side that can talk to the exchange).
    """
    threshold = f"reconciled, every position stopped, < {MAX_MISSED_RUNS} missed runs"
    naked = [p for p in positions(state) if p.get("stop_present") is False]
    if naked:
        symbols = ", ".join(str(p.get("symbol", "?")) for p in naked)
        return _result(
            "K4",
            "triggered",
            f"{len(naked)} position(s) without a stop",
            threshold,
            "flat",
            f"no stop on {symbols}",
        )
    if state is not None and state.reconciliation == "mismatch":
        return _result(
            "K4",
            "triggered",
            "mismatch",
            threshold,
            "alert",
            state.reconciliation_detail or "bot and exchange disagree on positions",
        )
    missed = missed_runs(bot, last_run, now)
    if missed >= MAX_MISSED_RUNS:
        return _result(
            "K4",
            "triggered",
            f"{missed} missed runs",
            threshold,
            "alert",
            f"{missed} schedule slots passed since the last run started",
        )
    reconciliation = state.reconciliation if state is not None else "unknown"
    return _result(
        "K4",
        "ok",
        reconciliation,
        threshold,
        "none",
        f"reconciliation {reconciliation}, {missed} missed runs",
    )


def k5_heartbeat(bot: Bot, now: datetime) -> dict:
    """No heartbeat by `schedule_every_s + grace_s` after the last one."""
    due = deadline(bot)
    last = bot.last_heartbeat
    value = last.isoformat() if last else "never"
    if now > due:
        overdue = now - due
        return _result(
            "K5",
            "triggered",
            value,
            due.isoformat(),
            "alert",
            f"no heartbeat since {value}, overdue by {overdue}",
        )
    return _result(
        "K5", "ok", value, due.isoformat(), "none", f"heartbeat due by {due.isoformat()}"
    )


def evaluate_all(
    bot: Bot,
    state: BotState | None,
    trades: list,
    now: datetime,
    stage_capital: Decimal,
    last_run: BotRun | None = None,
) -> list[dict]:
    """All five rules, in order. `trades` are the bot's closed trades from
    `status.bot_trades`, oldest first.
    """
    return [
        k1_capital(state, stage_capital),
        k2_edge(trades),
        k3_streak(trades),
        k4_integrity(bot, state, last_run, now),
        k5_heartbeat(bot, now),
    ]


# ponytail: the alert severity per rule is a five-entry table rather than a
# field on the result — nothing else varies with it, and monitor.py is the
# only reader.
CRITICAL_RULES = frozenset({"K1", "K4", "K5"})
