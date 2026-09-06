"""Obsidian note export/import endpoints — thin wrappers over `notes.py`."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import notes
from ..db import get_session
from ..settings import get_settings
from ._common import get_account_or_404
from .schemas import BaseModel

router = APIRouter()


class ExportIn(BaseModel):
    dir: str | None = None


class ExportOut(BaseModel):
    files: int


class ImportIn(BaseModel):
    dir: str
    account_id: int


class ImportOut(BaseModel):
    created: int
    updated: int
    skipped: int


@router.post("/notes/export", response_model=ExportOut)
def export_notes(payload: ExportIn, session: Session = Depends(get_session)):
    out_dir = payload.dir or get_settings().NOTES_OUT_DIR
    files = notes.export_all(session, out_dir)
    return ExportOut(files=len(files))


@router.post("/notes/import", response_model=ImportOut)
def import_notes(payload: ImportIn, session: Session = Depends(get_session)):
    get_account_or_404(session, payload.account_id)
    created, updated, skipped = notes.import_dir(session, payload.dir, payload.account_id)
    return ImportOut(created=created, updated=updated, skipped=skipped)
