"""FastAPI app factory. Static frontend serving is added in a later task."""

from __future__ import annotations

from fastapi import FastAPI

from .api import ROUTERS
from .db import init_db


def create_app(db_path: str | None = None) -> FastAPI:
    init_db(db_path)
    app = FastAPI(title="trade-ledger")
    for router in ROUTERS:
        app.include_router(router, prefix="/api")
    return app
