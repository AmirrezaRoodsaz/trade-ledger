"""Results per stage and the 0–100 % readiness score of one bot.

Three columns with the same story: the latest passed backtest, the journal
over the bot's demo/paper trades (incubation) and over its live trades. On
top of them the four weighted stages — backtest, drills, incubation, live —
each stage's progress capped by a gate factor, so a stage whose stage gate
fails can never look finished.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import analytics
from ..enums import Mode
from ..models import Account, BacktestResult, Bot, BotDrill, Setting, Trade

DRILL_KEYS = ("capability_test", "dry_run", "self_check", "recovery_drill", "scheduling")

_WEIGHT_DEFAULTS = {
    "readiness_w_backtest": Decimal(25),
    "readiness_w_drills": Decimal(15),
    "readiness_w_incubation": Decimal(30),
    "readiness_w_live": Decimal(30),
}
_TARGET_DEFAULTS = {
    "readiness_demo_trades": Decimal(30),
    "readiness_live_trades": Decimal(50),
}
_CAPITAL_DEFAULTS = {"paper": Decimal(2500), "live": Decimal(250)}
_HALF = Decimal("0.5")
_ONE = Decimal(1)


def _setting(session: Session, key: str, default: Decimal) -> Decimal:
    row = session.get(Setting, key)
    return Decimal(row.value) if row is not None else default


def _capital(session: Session, bot: Bot, gate_mode: str) -> Decimal:
    if bot.stage_capital_eur:
        return bot.stage_capital_eur
    return _setting(session, f"stage_capital_{gate_mode}", _CAPITAL_DEFAULTS[gate_mode])


def _bot_trades(session: Session, bot: Bot, modes: tuple[str, ...]) -> list[Trade]:
    stmt = (
        select(Trade)
        .join(Account, Account.id == Trade.account_id)
        .where(Trade.bot_id == bot.id, Account.mode.in_(modes))
    )
    return analytics.closed(session.execute(stmt).scalars().all())


def _stage_result(session: Session, bot: Bot, trades: list[Trade], gate_mode: str) -> dict:
    stats = analytics.compute_stats(trades)
    return {
        "count": stats.count,
        "expectancy_r": stats.expectancy_r,
        "profit_factor": stats.profit_factor,
        "win_rate": stats.win_rate,
        "max_dd_r": stats.max_dd_r,
        "max_dd_eur": stats.max_dd_eur,
        "adherence": stats.adherence,
        "gate": analytics.stage_gate(trades, gate_mode, _capital(session, bot, gate_mode)),
    }


def latest_passed_backtest(session: Session, bot: Bot) -> BacktestResult | None:
    """The bot's own passed result, else one for its strategy and preset,
    else any passed result for its strategy. Newest first.
    """
    wheres = [BacktestResult.bot_id == bot.id]
    if bot.preset_version_id is not None:
        wheres.append(
            (BacktestResult.strategy == bot.strategy)
            & (BacktestResult.preset_version_id == bot.preset_version_id)
        )
    wheres.append(BacktestResult.strategy == bot.strategy)

    for where in wheres:
        found = session.execute(
            select(BacktestResult)
            .where(BacktestResult.passed.is_(True), where)
            .order_by(BacktestResult.created.desc(), BacktestResult.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if found is not None:
            return found
    return None


def stage_results(session: Session, bot: Bot) -> dict:
    """`{backtest, incubation, live}`. A bot has one account, so one of the
    two journal columns is normally empty — both are computed anyway, since
    the same bot moves from demo to live without changing identity.
    """
    return {
        "backtest": latest_passed_backtest(session, bot),
        "incubation": _stage_result(
            session, bot, _bot_trades(session, bot, (Mode.PAPER, Mode.DEMO)), "paper"
        ),
        "live": _stage_result(session, bot, _bot_trades(session, bot, (Mode.LIVE,)), "live"),
    }


def drills(session: Session, bot: Bot) -> list[dict]:
    """All five drills, whether or not a row exists yet."""
    rows = {
        row.key: row
        for row in session.execute(select(BotDrill).where(BotDrill.bot_id == bot.id)).scalars()
    }
    return [
        {
            "key": key,
            "done": bool(rows[key].done) if key in rows else False,
            "done_ts": rows[key].done_ts if key in rows else None,
            "note": rows[key].note if key in rows else None,
        }
        for key in DRILL_KEYS
    ]


def set_drill(session: Session, bot: Bot, key: str, done: bool, note: str | None) -> BotDrill:
    row = session.execute(
        select(BotDrill).where(BotDrill.bot_id == bot.id, BotDrill.key == key)
    ).scalar_one_or_none()
    if row is None:
        row = BotDrill(bot_id=bot.id, key=key)
        session.add(row)
    row.done = done
    row.done_ts = datetime.now(UTC) if done else None
    row.note = note
    session.commit()
    return row


def _gate_factor(result: dict, gate_mode: str) -> Decimal:
    """1 while the gate passes or has too few trades to judge, 0,5 once it
    fails on something the trade count is not to blame for.
    """
    # ponytail: the gate's own minimum decides what "too few to judge" means,
    # so readiness never has a second opinion about it.
    if result["gate"]["passed"] or result["count"] < analytics._MIN_TRADES[gate_mode]:
        return _ONE
    return _HALF


def _progress(count: int, target: Decimal) -> Decimal:
    if target <= 0:
        return _ONE
    return min(_ONE, Decimal(count) / target)


def readiness(session: Session, bot: Bot, results: dict | None = None) -> dict:
    """`{percent, stages: [...]}` — percent is the weighted sum of
    `progress × factor`, rounded to one decimal.
    """
    results = results if results is not None else stage_results(session, bot)
    weights = {key: _setting(session, key, default) for key, default in _WEIGHT_DEFAULTS.items()}
    targets = {key: _setting(session, key, default) for key, default in _TARGET_DEFAULTS.items()}
    demo_target, live_target = targets["readiness_demo_trades"], targets["readiness_live_trades"]

    backtest = results["backtest"]
    done_drills = sum(1 for drill in drills(session, bot) if drill["done"])
    incubation, live = results["incubation"], results["live"]

    stages = [
        {
            "key": "backtest",
            "weight": weights["readiness_w_backtest"],
            "progress": _ONE if backtest is not None else Decimal(0),
            "factor": _ONE,
            "detail": f"passed: {backtest.label or backtest.strategy}"
            if backtest is not None
            else "no passed backtest",
        },
        {
            "key": "drills",
            "weight": weights["readiness_w_drills"],
            "progress": Decimal(done_drills) / len(DRILL_KEYS),
            "factor": _ONE,
            "detail": f"{done_drills} / {len(DRILL_KEYS)} drills done",
        },
        {
            "key": "incubation",
            "weight": weights["readiness_w_incubation"],
            "progress": _progress(incubation["count"], demo_target),
            "factor": _gate_factor(incubation, "paper"),
            "detail": f"{incubation['count']} / {demo_target:f} demo trades",
        },
        {
            "key": "live",
            "weight": weights["readiness_w_live"],
            "progress": _progress(live["count"], live_target),
            "factor": _gate_factor(live, "live"),
            "detail": f"{live['count']} / {live_target:f} live trades",
        },
    ]
    percent = sum(
        (stage["weight"] * stage["progress"] * stage["factor"] for stage in stages), Decimal(0)
    )
    return {"percent": percent.quantize(Decimal("0.1")), "stages": stages}
