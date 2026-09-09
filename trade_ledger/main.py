"""FastAPI app factory: the API under `/api`, uploaded screenshots under
`/screenshots`, and the built frontend (`frontend/dist`) at `/` with an SPA
fallback so a hard reload of `/journal` still lands on `index.html`.

`background=True` also runs the bot monitor for as long as the app lives.
It defaults to off so a test client — or any script that just wants the
routes — never starts a task that writes to the database behind its back.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import ROUTERS
from .bots import monitor
from .db import init_db
from .settings import get_settings

_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
_BUILD_HINT = "frontend not built: cd frontend && npm ci && npm run build"


@contextlib.asynccontextmanager
async def _background(app: FastAPI):
    """Start the monitor loop with the app and cancel it on shutdown."""
    task = asyncio.create_task(monitor.loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def create_app(db_path: str | None = None, *, background: bool = False) -> FastAPI:
    init_db(db_path)
    app = FastAPI(title="trade-ledger", lifespan=_background if background else None)
    for router in ROUTERS:
        app.include_router(router, prefix="/api")

    # Trade screenshots live under DATA_DIR/screenshots and are stored as
    # "screenshots/<trade id>/<file>", so the URL is just "/" + that path.
    # ponytail: check_dir=False rather than creating the directory here, which
    # would make every create_app (i.e. every test) write into the working
    # directory. The upload route mkdirs on the first upload, and a path can
    # only exist in trade.screenshots once that has happened. Ceiling: until
    # then StaticFiles answers a hand-typed /screenshots/... URL with a 500
    # rather than a 404 (starlette 1.6). Pre-create the directory if that ever
    # shows up in a log that matters.
    screenshots = Path(get_settings().DATA_DIR) / "screenshots"
    app.mount(
        "/screenshots",
        StaticFiles(directory=screenshots, check_dir=False),
        name="screenshots",
    )

    index = _DIST / "index.html"

    if not index.is_file():

        @app.get("/", include_in_schema=False)
        def build_hint() -> dict[str, str]:
            return {"detail": _BUILD_HINT}

        return app

    dist = _DIST.resolve()

    # Registered last, so every `/api/...` and `/screenshots/...` route above
    # wins. Anything else is a real file in dist or an SPA route.
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist):
            return FileResponse(candidate)
        return FileResponse(index)

    return app
