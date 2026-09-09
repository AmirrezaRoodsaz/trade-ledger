"""Presets and their versions. Versions are immutable: there is no PUT
route for one, so a hand-typed `PUT /api/presets/{id}/versions/{version_id}`
falls through to Starlette's default 405 for the `GET` path defined below.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..bots import presets
from ..db import get_session
from ..models import Preset, PresetVersion
from .schemas import BaseModel, Money

router = APIRouter()


def get_preset_or_404(session: Session, preset_id: int) -> Preset:
    preset = session.get(Preset, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="preset not found")
    return preset


class PresetIn(BaseModel):
    name: str
    strategy: str
    description: str | None = None


class PresetOut(BaseModel):
    id: int
    name: str
    strategy: str
    description: str | None


class PresetVersionIn(BaseModel):
    params: dict = Field(default_factory=dict)
    timeframe: str = "4h"
    pairs: list[str] = Field(default_factory=list)
    risk_pct: Money | None = None
    max_position_pct: Money | None = None
    leverage_cap: Money = Decimal(1)
    note: str | None = None


class PresetVersionOut(BaseModel):
    id: int
    preset_id: int
    version: int
    params: dict
    timeframe: str
    pairs: list[str]
    risk_pct: Money | None
    max_position_pct: Money | None
    leverage_cap: Money
    note: str | None
    created: datetime


def _version_out(version: PresetVersion) -> PresetVersionOut:
    return PresetVersionOut(
        id=version.id,
        preset_id=version.preset_id,
        version=version.version,
        params=json.loads(version.params_json),
        timeframe=version.timeframe,
        pairs=json.loads(version.pairs_json),
        risk_pct=version.risk_pct,
        max_position_pct=version.max_position_pct,
        leverage_cap=version.leverage_cap,
        note=version.note,
        created=version.created,
    )


@router.get("/presets", response_model=list[PresetOut])
def list_presets(session: Session = Depends(get_session)):
    return session.execute(select(Preset).order_by(Preset.id)).scalars().all()


@router.post("/presets", response_model=PresetOut, status_code=201)
def create_preset(payload: PresetIn, session: Session = Depends(get_session)):
    if session.execute(select(Preset.id).where(Preset.name == payload.name)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"preset name already taken: {payload.name}")
    return presets.create(session, payload.name, payload.strategy, payload.description)


@router.get("/presets/{preset_id}", response_model=PresetOut)
def get_preset(preset_id: int, session: Session = Depends(get_session)):
    return get_preset_or_404(session, preset_id)


@router.get("/presets/{preset_id}/versions", response_model=list[PresetVersionOut])
def list_versions(preset_id: int, session: Session = Depends(get_session)):
    get_preset_or_404(session, preset_id)
    versions = (
        session.execute(
            select(PresetVersion)
            .where(PresetVersion.preset_id == preset_id)
            .order_by(PresetVersion.version)
        )
        .scalars()
        .all()
    )
    return [_version_out(one) for one in versions]


@router.post("/presets/{preset_id}/versions", response_model=PresetVersionOut, status_code=201)
def create_version(
    preset_id: int, payload: PresetVersionIn, session: Session = Depends(get_session)
):
    preset = get_preset_or_404(session, preset_id)
    version = presets.add_version(
        session,
        preset,
        payload.params,
        payload.timeframe,
        payload.pairs,
        payload.risk_pct,
        payload.max_position_pct,
        payload.leverage_cap,
        payload.note,
    )
    return _version_out(version)


@router.get("/presets/{preset_id}/versions/{version_id}", response_model=PresetVersionOut)
def get_version(preset_id: int, version_id: int, session: Session = Depends(get_session)):
    """The only other verb on this path is the 404 above for a bad id — a
    `PUT` here has nowhere else defined and gets Starlette's plain 405,
    which is the immutability guarantee versions get.
    """
    get_preset_or_404(session, preset_id)
    version = session.get(PresetVersion, version_id)
    if version is None or version.preset_id != preset_id:
        raise HTTPException(status_code=404, detail="preset version not found")
    return _version_out(version)
