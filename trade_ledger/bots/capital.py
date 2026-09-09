"""Which capital a bot is measured against.

Lives here rather than in a router because both the push protocol (the
config a bot pulls) and the app-side kill rules need the same answer.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..enums import Mode
from ..models import Bot, Setting

STAGE_CAPITAL_DEFAULTS = {
    Mode.PAPER: Decimal(2500),
    Mode.DEMO: Decimal(2500),
    Mode.LIVE: Decimal(250),
}


def stage_capital_eur(session: Session, bot: Bot, mode: str) -> Decimal:
    """Bot override, else the per-mode Setting, else the ladder's default."""
    if bot.stage_capital_eur is not None:
        return bot.stage_capital_eur
    setting = session.get(Setting, f"stage_capital_{mode}")
    if setting is not None:
        try:
            return Decimal(setting.value)
        except InvalidOperation as exc:
            raise HTTPException(
                status_code=500, detail=f"invalid stage_capital setting: {setting.key}"
            ) from exc
    return STAGE_CAPITAL_DEFAULTS[mode]
