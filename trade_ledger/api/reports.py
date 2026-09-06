"""Weekly PDF report routes: build one, list what's on disk, download one.

`name` on the download route is validated against the exact filename shape
`build_weekly` produces, so a request can't escape the reports directory
(`../../etc/passwd` fails the pattern before it ever touches the filesystem).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..reports.weekly import build_weekly
from ..settings import get_settings
from .schemas import BaseModel

router = APIRouter(prefix="/reports")

_NAME_RE = re.compile(r"^weekly-\d{4}-\d{2}-\d{2}-(live|paper|demo|all)\.pdf$")


def _reports_dir() -> Path:
    return Path(get_settings().DATA_DIR) / "exports" / "reports"


class WeeklyRequest(BaseModel):
    week_end: date | None = None
    mode: str = "paper"


class WeeklyResponse(BaseModel):
    path: str
    url: str
    name: str


class ReportEntry(BaseModel):
    name: str
    size: int
    created: float


@router.post("/weekly", response_model=WeeklyResponse)
def post_weekly(body: WeeklyRequest, session: Session = Depends(get_session)):
    if body.mode not in ("live", "paper", "demo", "all"):
        raise HTTPException(status_code=422, detail=f"unknown mode: {body.mode}")
    week_end = body.week_end or datetime.now(UTC).date()
    path = build_weekly(session, week_end, body.mode)
    return WeeklyResponse(path=str(path), url=f"/api/reports/{path.name}", name=path.name)


@router.get("", response_model=list[ReportEntry])
def list_reports():
    reports_dir = _reports_dir()
    if not reports_dir.exists():
        return []
    entries = [
        ReportEntry(name=f.name, size=f.stat().st_size, created=f.stat().st_mtime)
        for f in sorted(reports_dir.glob("*.pdf"))
    ]
    return entries


@router.get("/{name}")
def get_report(name: str):
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=422, detail="invalid report name")
    path = _reports_dir() / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="report not found")
    return FileResponse(path, media_type="application/pdf", filename=name)
