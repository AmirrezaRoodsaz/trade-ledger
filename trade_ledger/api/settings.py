from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Setting
from ..settings import env_status as _env_status
from .schemas import BaseModel

router = APIRouter()


class SettingItem(BaseModel):
    key: str
    value: str


@router.get("/settings", response_model=list[SettingItem])
def list_settings(session: Session = Depends(get_session)):
    return session.execute(select(Setting)).scalars().all()


@router.put("/settings", response_model=list[SettingItem])
def put_settings(payload: list[SettingItem], session: Session = Depends(get_session)):
    for item in payload:
        existing = session.get(Setting, item.key)
        if existing is not None:
            existing.value = item.value
        else:
            session.add(Setting(key=item.key, value=item.value))
    session.commit()
    return session.execute(select(Setting)).scalars().all()


@router.get("/settings/env-status", response_model=dict[str, bool])
def get_env_status():
    return _env_status()
