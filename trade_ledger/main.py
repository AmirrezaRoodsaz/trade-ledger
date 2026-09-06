"""FastAPI app factory: the API under `/api`, uploaded screenshots under
`/screenshots`, and the built frontend (`frontend/dist`) at `/` with an SPA
fallback so a hard reload of `/journal` still lands on `index.html`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import ROUTERS
from .db import init_db
from .settings import get_settings

_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
_BUILD_HINT = "frontend not built: cd frontend && npm ci && npm run build"


def create_app(db_path: str | None = None) -> FastAPI:
    init_db(db_path)
    app = FastAPI(title="trade-ledger")
    for router in ROUTERS:
        app.include_router(router, prefix="/api")

    # Trade screenshots live under DATA_DIR/screenshots and are stored as
    # "screenshots/<trade id>/<file>", so the URL is just "/" + that path.
    # Created here rather than left to the first upload: StaticFiles 500s on a
    # missing directory instead of 404ing.
    screenshots = Path(get_settings().DATA_DIR) / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    app.mount("/screenshots", StaticFiles(directory=screenshots), name="screenshots")

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
