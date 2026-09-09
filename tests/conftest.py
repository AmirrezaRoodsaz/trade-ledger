from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trade_ledger import db
from trade_ledger.bots.auth import issue_token
from trade_ledger.enums import AccountKind, AssetClass, BotHost, Mode, Venue
from trade_ledger.main import create_app
from trade_ledger.models import Account, Bot, Instrument


@pytest.fixture()
def session():
    """Fresh in-memory SQLite DB per test, tables created."""
    db.init_db(":memory:")
    with db.SessionLocal() as s:
        yield s


@pytest.fixture()
def account_factory(session):
    def make(**kwargs) -> Account:
        defaults = {
            "venue": Venue.OKX,
            "name": f"acct-{uuid4().hex[:8]}",
            "kind": AccountKind.CRYPTO_SPOT,
            "mode": Mode.PAPER,
        }
        defaults.update(kwargs)
        account = Account(**defaults)
        session.add(account)
        session.flush()
        return account

    return make


@pytest.fixture()
def instrument_factory(session):
    def make(**kwargs) -> Instrument:
        defaults = {
            "symbol": f"SYM{uuid4().hex[:6].upper()}",
            "asset_class": AssetClass.CRYPTO,
        }
        defaults.update(kwargs)
        instrument = Instrument(**defaults)
        session.add(instrument)
        session.flush()
        return instrument

    return make


@pytest.fixture()
def bot_factory(session, account_factory):
    """`(bot, token)` — the plaintext token is only knowable at creation."""

    def make(**kwargs) -> tuple[Bot, str]:
        token, token_hash = issue_token()
        name = kwargs.pop("name", f"bot-{uuid4().hex[:8]}")
        defaults = {
            "slug": name,
            "name": name,
            "strategy": "donchian",
            "host": BotHost.LOCAL,
            "token_hash": token_hash,
        }
        defaults.update(kwargs)
        if "account_id" not in defaults:
            defaults["account_id"] = account_factory().id
        bot = Bot(**defaults)
        session.add(bot)
        session.flush()
        return bot, token

    return make


@pytest.fixture()
def client(session):
    """TestClient whose `get_session` dependency is overridden to the `session` fixture."""
    app = create_app(db_path=":memory:")

    def _override_get_session():
        yield session

    app.dependency_overrides[db.get_session] = _override_get_session
    with TestClient(app) as test_client:
        yield test_client
