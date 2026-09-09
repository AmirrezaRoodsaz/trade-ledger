"""FastAPI app factory: the API under `/api`, uploaded screenshots under
`/screenshots`, and the built frontend (`frontend/dist`) at `/` with an SPA
fallback so a hard reload of `/journal` still lands on `index.html`.

`background=True` also runs the bot monitor and the local supervisor for
as long as the app lives.
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
from .bots import alerts, monitor, supervisor, telegram
from .db import init_db
from .settings import get_settings

_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
_BUILD_HINT = "frontend not built: cd frontend && npm ci && npm run build"


def _telegram_configured() -> bool:
    settings = get_settings()
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)


@contextlib.asynccontextmanager
async def _background(app: FastAPI):
    """Start the monitor and supervisor loops with the app, plus the Telegram
    poller when Telegram is configured, and cancel them all on shutdown.

    The supervisor's launch hook lives here too: only a process that is
    actually running the supervisor may promise that a `run_now` starts
    something. Likewise the Telegram `notify` registration happens here, not
    at module import, so a plain `pytest` run with real keys in `.env` never
    wires up a notifier that would send real alerts. `NOTIFIERS` is a
    module-level list shared by every app instance, so the append is
    idempotent.
    """
    supervisor.register_hook()
    tasks = [asyncio.create_task(monitor.loop()), asyncio.create_task(supervisor.loop())]
    if _telegram_configured():
        if telegram.notify not in alerts.NOTIFIERS:
            alerts.NOTIFIERS.append(telegram.notify)
        tasks.append(asyncio.create_task(telegram.loop()))
    try:
        yield
    finally:
        # ponytail: the loops are cancelled, running bots are not. A bot is a
        # separate process in its own session and may be mid-order; killing it
        # on a restart of the app is how a position ends up without a stop.
        # It finishes, writes its log, and the next supervisor start reaps
        # nothing — the run row it opened is the monitor's problem (K4).
        supervisor.unregister_hook()
        for task in tasks:
            task.cancel()
        for task in tasks:
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
