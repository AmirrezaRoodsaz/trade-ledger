"""Playbooks and their versions, the tag vocabulary, and daily notes — the
journal's supporting rows. One module because each is a handful of plain CRUD
routes over a single table.
"""

from __future__ import annotations

from datetime import date as date_
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import DailyNote, Playbook, PlaybookVersion, Tag
from .schemas import BaseModel, Money

router = APIRouter()


class PlaybookIn(BaseModel):
    name: str


class PlaybookOut(PlaybookIn):
    id: int
    created: datetime


class PlaybookVersionIn(BaseModel):
    rules_md: str
    source_link: str | None = None


class PlaybookVersionOut(PlaybookVersionIn):
    id: int
    playbook_id: int
    version: int
    created: datetime


class TagIn(BaseModel):
    kind: str
    name: str


class TagOut(TagIn):
    id: int


class DailyNoteIn(BaseModel):
    text: str
    mood: str | None = None
    hours_spent: Money | None = None


class DailyNoteOut(DailyNoteIn):
    id: int
    date: date_


def _get_playbook_or_404(session: Session, playbook_id: int) -> Playbook:
    playbook = session.get(Playbook, playbook_id)
    if playbook is None:
        raise HTTPException(status_code=404, detail="playbook not found")
    return playbook


@router.get("/playbooks", response_model=list[PlaybookOut])
def list_playbooks(session: Session = Depends(get_session)):
    return session.execute(select(Playbook).order_by(Playbook.name)).scalars().all()


@router.post("/playbooks", response_model=PlaybookOut, status_code=201)
def create_playbook(payload: PlaybookIn, session: Session = Depends(get_session)):
    exists = session.execute(
        select(Playbook.id).where(Playbook.name == payload.name)
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail="playbook name already exists")
    playbook = Playbook(name=payload.name)
    session.add(playbook)
    session.commit()
    session.refresh(playbook)
    return playbook


@router.get("/playbooks/{playbook_id}/versions", response_model=list[PlaybookVersionOut])
def list_playbook_versions(playbook_id: int, session: Session = Depends(get_session)):
    _get_playbook_or_404(session, playbook_id)
    return (
        session.execute(
            select(PlaybookVersion)
            .where(PlaybookVersion.playbook_id == playbook_id)
            .order_by(PlaybookVersion.version)
        )
        .scalars()
        .all()
    )


@router.post(
    "/playbooks/{playbook_id}/versions", response_model=PlaybookVersionOut, status_code=201
)
def create_playbook_version(
    playbook_id: int, payload: PlaybookVersionIn, session: Session = Depends(get_session)
):
    """Versions are append-only: a rule change is a new version, never an edit."""
    _get_playbook_or_404(session, playbook_id)
    highest = session.scalar(
        select(func.max(PlaybookVersion.version)).where(PlaybookVersion.playbook_id == playbook_id)
    )
    version = PlaybookVersion(
        playbook_id=playbook_id, version=(highest or 0) + 1, **payload.model_dump()
    )
    session.add(version)
    session.commit()
    session.refresh(version)
    return version


@router.get("/tags", response_model=list[TagOut])
def list_tags(kind: str | None = None, session: Session = Depends(get_session)):
    stmt = select(Tag).order_by(Tag.kind, Tag.name)
    if kind is not None:
        stmt = stmt.where(Tag.kind == kind)
    return session.execute(stmt).scalars().all()


@router.post("/tags", response_model=TagOut, status_code=201)
def create_tag(payload: TagIn, session: Session = Depends(get_session)):
    exists = session.execute(
        select(Tag.id).where(Tag.kind == payload.kind, Tag.name == payload.name)
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail="tag already exists")
    tag = Tag(**payload.model_dump())
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return tag


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(tag_id: int, session: Session = Depends(get_session)):
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="tag not found")
    session.delete(tag)
    session.commit()


@router.get("/daily-notes", response_model=list[DailyNoteOut])
def list_daily_notes(
    date_from: date_ | None = Query(None, alias="from"),
    date_to: date_ | None = Query(None, alias="to"),
    session: Session = Depends(get_session),
):
    stmt = select(DailyNote).order_by(DailyNote.date)
    if date_from is not None:
        stmt = stmt.where(DailyNote.date >= date_from)
    if date_to is not None:
        stmt = stmt.where(DailyNote.date <= date_to)
    return session.execute(stmt).scalars().all()


@router.get("/daily-notes/{note_date}", response_model=DailyNoteOut)
def get_daily_note(note_date: date_, session: Session = Depends(get_session)):
    note = session.execute(
        select(DailyNote).where(DailyNote.date == note_date)
    ).scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail="daily note not found")
    return note


@router.put("/daily-notes/{note_date}", response_model=DailyNoteOut)
def put_daily_note(note_date: date_, payload: DailyNoteIn, session: Session = Depends(get_session)):
    """Upsert — one note per day, written or rewritten in place."""
    note = session.execute(
        select(DailyNote).where(DailyNote.date == note_date)
    ).scalar_one_or_none()
    if note is None:
        note = DailyNote(date=note_date, **payload.model_dump())
        session.add(note)
    else:
        for field, value in payload.model_dump().items():
            setattr(note, field, value)
    session.commit()
    session.refresh(note)
    return note
