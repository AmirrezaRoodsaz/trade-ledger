"""Engine, session factory and custom column types.

`SessionLocal` is bound lazily: importing this module opens no file and no
engine. Call `init_db()` once (the app on startup, tests via the `session`
fixture) before using `SessionLocal` or the `get_session()` dependency.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import String, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from .settings import get_settings


class Base(DeclarativeBase):
    pass


class Money(TypeDecorator):
    """`Decimal` stored as fixed-point text — never a float, never sci-notation."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return format(Decimal(value), "f")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(value)


class UTCDateTime(TypeDecorator):
    """Aware `datetime` stored as ISO-8601 text with a `+00:00` offset."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        return value.astimezone(UTC).isoformat()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return datetime.fromisoformat(value)


engine = None
SessionLocal: sessionmaker[Session] | None = None


def init_db(path: str | Path | None = None) -> None:
    """(Re)bind the engine to `path`, create tables, seed `schema_version`.

    `path` of `":memory:"` opens an in-memory SQLite DB (used by tests).
    """
    global engine, SessionLocal

    if path is None:
        path = get_settings().DB_PATH
    path = str(path)

    if path == ":memory:":
        url = "sqlite:///:memory:"
        # ponytail: StaticPool pins the whole engine to one connection. Plain
        # SQLite `:memory:` is one DB per connection, and FastAPI's TestClient
        # runs endpoints in a worker thread — without this, that thread opens
        # a second, empty in-memory DB instead of reusing the test's tables.
        engine = create_engine(
            url, connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    else:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.touch(exist_ok=True)
        url = f"sqlite:///{file_path}"
        engine = create_engine(url, connect_args={"check_same_thread": False})

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    # Deferred import: models.py imports Base from this module, and importing
    # it here (before create_all) is what registers every table on
    # Base.metadata — without it, a process that calls init_db() without
    # already having imported trade_ledger.models gets an empty schema.
    from .models import Setting

    Base.metadata.create_all(engine)

    with SessionLocal() as session:
        if session.get(Setting, "schema_version") is None:
            session.add(Setting(key="schema_version", value="1"))
            session.commit()


def get_session():
    """FastAPI dependency yielding a `Session`. Call `init_db()` first."""
    if SessionLocal is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
