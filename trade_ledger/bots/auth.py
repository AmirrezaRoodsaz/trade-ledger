"""Bot bearer tokens.

A token is `secrets.token_urlsafe(32)`, shown once on create and on rotate.
Only its sha256 hex is stored, so a leaked database cannot be used to talk to
the app as a bot.
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Bot


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token() -> tuple[str, str]:
    """`(token, token_hash)` — the caller stores the hash and shows the token."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def verify(session: Session, token: str) -> Bot | None:
    """The bot this token belongs to, or `None` if it belongs to no bot."""
    if not token:
        return None
    return session.execute(
        select(Bot).where(Bot.token_hash == hash_token(token))
    ).scalar_one_or_none()


def bot_auth(request: Request, session: Session = Depends(get_session)) -> Bot | None:
    """FastAPI dependency: the calling bot, or `None` for the local UI.

    No `Authorization` header means the local UI, whose behaviour is
    unchanged. A bearer token that matches no bot is a 401 — a bot with a
    stale token must fail loudly, not silently act as the UI.
    """
    header = request.headers.get("Authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    bot = verify(session, token.strip())
    if bot is None:
        raise HTTPException(status_code=401, detail="invalid bot token")
    return bot
