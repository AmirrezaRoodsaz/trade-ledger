"""ORM tables. Enum columns store the enum's string value as plain text —
`StrEnum` members compare equal to their raw string, so callers can pass or
compare either without a custom SQLAlchemy Enum type.
"""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as date_
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, Money, UTCDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    venue: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    base_ccy: Mapped[str] = mapped_column(String, default="EUR")
    credential_env_prefix: Mapped[str | None] = mapped_column(String, default=None)
    tax_wallet: Mapped[str] = mapped_column(String, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)

    def __init__(self, **kwargs):
        # ponytail: default in __init__ rather than a DB-side default so it
        # can see the sibling `name` kwarg at construction time.
        if not kwargs.get("tax_wallet"):
            kwargs["tax_wallet"] = kwargs.get("name")
        super().__init__(**kwargs)


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("symbol", "asset_class"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)
    isin: Mapped[str | None] = mapped_column(String, default=None)
    quote_ccy: Mapped[str] = mapped_column(String, default="EUR")
    name: Mapped[str | None] = mapped_column(String, default=None)
    price_source: Mapped[str | None] = mapped_column(String, default=None)
    price_symbol: Mapped[str | None] = mapped_column(String, default=None)
    fund_type: Mapped[str | None] = mapped_column(String, default=None)
    tax_regime: Mapped[str | None] = mapped_column(String, default=None)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("account_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"), default=None)
    quantity: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    price: Mapped[Decimal | None] = mapped_column(Money, default=None)
    price_ccy: Mapped[str | None] = mapped_column(String, default=None)
    fee: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    fee_ccy: Mapped[str | None] = mapped_column(String, default=None)
    fx_rate: Mapped[Decimal | None] = mapped_column(Money, default=None)
    fx_source: Mapped[str | None] = mapped_column(String, default=None)
    amount_eur: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    fee_eur: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    withholding_tax_eur: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    external_id: Mapped[str | None] = mapped_column(String, default=None)
    link_id: Mapped[str | None] = mapped_column(String, default=None)
    source: Mapped[str] = mapped_column(String, nullable=False)
    raw_json: Mapped[str | None] = mapped_column(String, default=None)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"), default=None)
    note: Mapped[str | None] = mapped_column(String, default=None)


class Playbook(Base):
    __tablename__ = "playbooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    created: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class PlaybookVersion(Base):
    __tablename__ = "playbook_versions"
    __table_args__ = (UniqueConstraint("playbook_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    playbook_id: Mapped[int] = mapped_column(ForeignKey("playbooks.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    rules_md: Mapped[str] = mapped_column(String, nullable=False)
    source_link: Mapped[str | None] = mapped_column(String, default=None)
    created: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="planned")
    playbook_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("playbook_versions.id"), default=None
    )

    planned_entry: Mapped[Decimal | None] = mapped_column(Money, default=None)
    planned_stop: Mapped[Decimal | None] = mapped_column(Money, default=None)
    planned_target: Mapped[Decimal | None] = mapped_column(Money, default=None)
    risk_eur: Mapped[Decimal | None] = mapped_column(Money, default=None)
    planned_qty: Mapped[Decimal | None] = mapped_column(Money, default=None)

    opened_ts: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    closed_ts: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    avg_entry: Mapped[Decimal | None] = mapped_column(Money, default=None)
    avg_exit: Mapped[Decimal | None] = mapped_column(Money, default=None)
    quantity: Mapped[Decimal | None] = mapped_column(Money, default=None)
    fees_eur: Mapped[Decimal | None] = mapped_column(Money, default=None)
    result_eur: Mapped[Decimal | None] = mapped_column(Money, default=None)
    mae_eur: Mapped[Decimal | None] = mapped_column(Money, default=None)
    mfe_eur: Mapped[Decimal | None] = mapped_column(Money, default=None)

    adherence: Mapped[bool | None] = mapped_column(Boolean, default=None)
    mistake: Mapped[str | None] = mapped_column(String, default=None)
    emotion_pre: Mapped[str | None] = mapped_column(String, default=None)
    emotion_post: Mapped[str | None] = mapped_column(String, default=None)
    note_pre: Mapped[str | None] = mapped_column(String, default=None)
    note_post: Mapped[str | None] = mapped_column(String, default=None)
    screenshots: Mapped[str] = mapped_column(String, default="[]")
    tags: Mapped[str] = mapped_column(String, default="[]")
    external_ref: Mapped[str | None] = mapped_column(String, default=None)
    bot_id: Mapped[int | None] = mapped_column(ForeignKey("bots.id"), default=None)

    @property
    def r_multiple(self) -> Decimal | None:
        if self.result_eur is None or not self.risk_eur:
            return None
        return self.result_eur / self.risk_eur

    @property
    def mae_r(self) -> Decimal | None:
        if self.mae_eur is None or not self.risk_eur:
            return None
        return self.mae_eur / self.risk_eur

    @property
    def mfe_r(self) -> Decimal | None:
        if self.mfe_eur is None or not self.risk_eur:
            return None
        return self.mfe_eur / self.risk_eur


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("kind", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # setup|mistake|market|emotion|other
    name: Mapped[str] = mapped_column(String, nullable=False)


class DailyNote(Base):
    __tablename__ = "daily_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date_] = mapped_column(Date, unique=True, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    mood: Mapped[str | None] = mapped_column(String, default=None)
    hours_spent: Mapped[Decimal | None] = mapped_column(Money, default=None)


class Price(Base):
    __tablename__ = "prices"
    __table_args__ = (UniqueConstraint("instrument_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    date: Mapped[date_] = mapped_column(Date, nullable=False)
    open: Mapped[Decimal | None] = mapped_column(Money, default=None)
    high: Mapped[Decimal | None] = mapped_column(Money, default=None)
    low: Mapped[Decimal | None] = mapped_column(Money, default=None)
    close: Mapped[Decimal | None] = mapped_column(Money, default=None)
    ccy: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("ccy", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ccy: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[date_] = mapped_column(Date, nullable=False)
    rate_to_eur: Mapped[Decimal] = mapped_column(Money, nullable=False)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    started: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    finished: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    status: Mapped[str] = mapped_column(String, default="running")  # running|ok|error
    added: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String, default=None)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class Bot(Base):
    """One registered bot process. `token_hash` is the sha256 hex of the
    bearer token — the token itself is shown once, on create and on rotate,
    and never stored.
    """

    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    strategy: Mapped[str] = mapped_column(String, nullable=False)
    preset_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("preset_versions.id"), default=None
    )
    host: Mapped[str] = mapped_column(String, default="local")
    schedule_every_s: Mapped[int] = mapped_column(Integer, default=14400)
    schedule_at: Mapped[str] = mapped_column(String, default="00:05")
    grace_s: Mapped[int] = mapped_column(Integer, default=3300)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    paused_entries: Mapped[bool] = mapped_column(Boolean, default=False)
    stage_capital_eur: Mapped[Decimal | None] = mapped_column(Money, default=None)
    token_hash: Mapped[str] = mapped_column(String, nullable=False)
    code_version: Mapped[str | None] = mapped_column(String, default=None)
    last_heartbeat: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    last_run_id: Mapped[int | None] = mapped_column(Integer, default=None)
    status: Mapped[str] = mapped_column(String, default="ok")
    created: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class Preset(Base):
    __tablename__ = "presets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    strategy: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, default=None)


class PresetVersion(Base):
    """Immutable once created — an edit is a new version."""

    __tablename__ = "preset_versions"
    __table_args__ = (UniqueConstraint("preset_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    preset_id: Mapped[int] = mapped_column(ForeignKey("presets.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    params_json: Mapped[str] = mapped_column(String, default="{}")
    timeframe: Mapped[str] = mapped_column(String, default="4h")
    pairs_json: Mapped[str] = mapped_column(String, default="[]")
    risk_pct: Mapped[Decimal | None] = mapped_column(Money, default=None)
    max_position_pct: Mapped[Decimal | None] = mapped_column(Money, default=None)
    leverage_cap: Mapped[Decimal] = mapped_column(Money, default=Decimal(1))
    note: Mapped[str | None] = mapped_column(String, default=None)
    created: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class BotRun(Base):
    __tablename__ = "bot_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), nullable=False)
    started: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    finished: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    status: Mapped[str] = mapped_column(String, default="running")
    summary_json: Mapped[str] = mapped_column(String, default="{}")
    log_path: Mapped[str | None] = mapped_column(String, default=None)
    error: Mapped[str | None] = mapped_column(String, default=None)


class BotState(Base):
    """Latest snapshot per bot — one row, upserted."""

    __tablename__ = "bot_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), unique=True, nullable=False)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    equity_eur: Mapped[Decimal | None] = mapped_column(Money, default=None)
    peak_equity_eur: Mapped[Decimal | None] = mapped_column(Money, default=None)
    positions_json: Mapped[str] = mapped_column(String, default="[]")
    open_orders_json: Mapped[str] = mapped_column(String, default="[]")
    reconciliation: Mapped[str] = mapped_column(String, default="unknown")
    reconciliation_detail: Mapped[str | None] = mapped_column(String, default=None)
    config_version: Mapped[int | None] = mapped_column(Integer, default=None)
    extra_json: Mapped[str] = mapped_column(String, default="{}")


class BotEvent(Base):
    __tablename__ = "bot_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), nullable=False)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(String, default="")
    payload_json: Mapped[str] = mapped_column(String, default="{}")


class BotCommand(Base):
    __tablename__ = "bot_commands"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(String, default=None)
    issued_ts: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    issued_by: Mapped[str] = mapped_column(String, default="ui")
    acked_ts: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    result: Mapped[str | None] = mapped_column(String, default=None)
    result_detail: Mapped[str | None] = mapped_column(String, default=None)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int | None] = mapped_column(ForeignKey("bots.id"), default=None)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(String, default="")
    sent_telegram: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class BacktestResult(Base):
    """One uploaded (or bot-pushed) backtest run. The app never backtests —
    it stores the numbers, judges them against the configured criteria and
    keeps the verdict in `passed` / `fail_reasons_json`.
    """

    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy: Mapped[str] = mapped_column(String, nullable=False)
    preset_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("preset_versions.id"), default=None
    )
    bot_id: Mapped[int | None] = mapped_column(ForeignKey("bots.id"), default=None)
    label: Mapped[str] = mapped_column(String, default="")
    period_start: Mapped[date_] = mapped_column(Date, nullable=False)
    period_end: Mapped[date_] = mapped_column(Date, nullable=False)
    data_source: Mapped[str] = mapped_column(String, default="")
    timeframe: Mapped[str] = mapped_column(String, default="4h")
    pairs_json: Mapped[str] = mapped_column(String, default="[]")
    costs_note: Mapped[str] = mapped_column(String, default="")
    trades: Mapped[int] = mapped_column(Integer, default=0)
    expectancy_r: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    profit_factor: Mapped[Decimal | None] = mapped_column(Money, default=None)
    win_rate: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    max_drawdown_pct: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    cagr_pct: Mapped[Decimal | None] = mapped_column(Money, default=None)
    benchmark_cagr_pct: Mapped[Decimal | None] = mapped_column(Money, default=None)
    benchmark_max_drawdown_pct: Mapped[Decimal | None] = mapped_column(Money, default=None)
    equity_json: Mapped[str | None] = mapped_column(String, default=None)
    notes: Mapped[str] = mapped_column(String, default="")
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    fail_reasons_json: Mapped[str] = mapped_column(String, default="[]")
    created: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)


class BotDrill(Base):
    """One manual checklist item per bot — the five drills of the readiness
    stage B. A row exists only once the drill has been touched.
    """

    __tablename__ = "bot_drills"
    __table_args__ = (UniqueConstraint("bot_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_ts: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    note: Mapped[str | None] = mapped_column(String, default=None)
