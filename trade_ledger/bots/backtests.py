"""Pass/fail for an uploaded backtest result.

The app never runs a backtest — a result arrives as JSON (upload or bot push)
and is judged here against the rules' kill criteria, configurable as
`Setting` rows. `evaluate` is pure: a dict of numbers in, a verdict out.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from ..models import Setting

DEFAULTS = {
    "bt_min_trades": "40",
    "bt_min_expectancy": "0",
    "bt_min_profit_factor": "1.3",
    "bt_max_drawdown_pct": "30",
    "bt_require_beat_benchmark": "true",
}


def criteria(session: Session) -> dict:
    """The configured criteria, `DEFAULTS` where no `Setting` row exists."""
    raw = {}
    for key, default in DEFAULTS.items():
        row = session.get(Setting, key)
        raw[key] = row.value if row is not None else default
    return {
        "min_trades": int(Decimal(raw["bt_min_trades"])),
        "min_expectancy": Decimal(raw["bt_min_expectancy"]),
        "min_profit_factor": Decimal(raw["bt_min_profit_factor"]),
        "max_drawdown_pct": Decimal(raw["bt_max_drawdown_pct"]),
        "require_beat_benchmark": raw["bt_require_beat_benchmark"].strip().lower()
        in ("1", "true", "yes", "on"),
    }


def _dec(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def evaluate(result: dict, criteria: dict) -> tuple[bool, list[str]]:
    """`(passed, reasons)`. Each reason names the criterion it failed, so the
    UI can show why without re-deriving anything.
    """
    reasons: list[str] = []

    trades = int(_dec(result.get("trades")) or 0)
    if trades < criteria["min_trades"]:
        reasons.append(f"min_trades: {trades} < {criteria['min_trades']}")

    expectancy = _dec(result.get("expectancy_r")) or Decimal(0)
    if expectancy <= criteria["min_expectancy"]:
        reasons.append(f"min_expectancy: {expectancy} not above {criteria['min_expectancy']}")

    profit_factor = _dec(result.get("profit_factor"))
    # ponytail: a missing profit factor is not a fail — a run without a
    # losing trade has none. Every other criterion still has to hold.
    if profit_factor is not None and profit_factor < criteria["min_profit_factor"]:
        reasons.append(f"min_profit_factor: {profit_factor} < {criteria['min_profit_factor']}")

    drawdown = _dec(result.get("max_drawdown_pct")) or Decimal(0)
    if drawdown > criteria["max_drawdown_pct"]:
        reasons.append(f"max_drawdown_pct: {drawdown} > {criteria['max_drawdown_pct']}")

    if criteria["require_beat_benchmark"]:
        reasons.extend(_benchmark_reasons(result, drawdown))

    return not reasons, reasons


def _benchmark_reasons(result: dict, drawdown: Decimal) -> list[str]:
    """Return/drawdown against the benchmark's. Skipped entirely when the
    benchmark fields are absent — most results carry none.
    """
    bench_cagr = _dec(result.get("benchmark_cagr_pct"))
    bench_dd = _dec(result.get("benchmark_max_drawdown_pct"))
    if bench_cagr is None or bench_dd is None or bench_dd <= 0:
        return []

    cagr = _dec(result.get("cagr_pct"))
    if cagr is None:
        return ["beat_benchmark: no cagr_pct to compare"]
    if drawdown <= 0:
        return []  # no drawdown at all beats any benchmark ratio

    ratio, bench_ratio = cagr / drawdown, bench_cagr / bench_dd
    if ratio < bench_ratio:
        return [f"beat_benchmark: CAGR ÷ max DD {ratio:.2f} < {bench_ratio:.2f}"]
    return []
