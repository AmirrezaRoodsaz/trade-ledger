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
