from __future__ import annotations

from uuid import uuid4

import pytest

from trade_ledger import db
from trade_ledger.enums import AccountKind, AssetClass, Mode, Venue
from trade_ledger.models import Account, Instrument


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
