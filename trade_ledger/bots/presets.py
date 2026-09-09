"""Presets: named, versioned parameter sets a bot runs with.

A `PresetVersion` is immutable once created — a parameter change is a new
version, never an edit (the API has no PUT for one). Assigning a version to
a bot is a config change the bot only sees at its next `GET config`; on a
live-mode bot the operator must type why, and the app records that
alongside the `config_applied` event and queues a `reload_config` command
so the fleet view can show "pending" until the bot acks.
"""

from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..enums import CommandKind, EventKind, Mode
from ..models import Account, Bot, BotEvent, Preset, PresetVersion
from . import commands
from .commands import CommandError


class PresetError(Exception):
    """A guard failed. `status_code` is the HTTP status the router maps it to."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def create(session: Session, name: str, strategy: str, description: str | None = None) -> Preset:
    preset = Preset(name=name, strategy=strategy, description=description)
    session.add(preset)
    session.commit()
    session.refresh(preset)
    return preset


def add_version(
    session: Session,
    preset: Preset,
    params: dict,
    timeframe: str,
    pairs: list[str],
    risk_pct: Decimal | None,
    max_position_pct: Decimal | None,
    leverage_cap: Decimal,
    note: str | None = None,
) -> PresetVersion:
    """Next free version number for this preset, starting at 1."""
    highest = session.execute(
        select(func.max(PresetVersion.version)).where(PresetVersion.preset_id == preset.id)
    ).scalar_one()
    version = PresetVersion(
        preset_id=preset.id,
        version=(highest or 0) + 1,
        params_json=json.dumps(params),
        timeframe=timeframe,
        pairs_json=json.dumps(pairs),
        risk_pct=risk_pct,
        max_position_pct=max_position_pct,
        leverage_cap=leverage_cap,
        note=note,
    )
    session.add(version)
    session.commit()
    session.refresh(version)
    return version


def assign(
    session: Session, bot: Bot, version: PresetVersion, reason: str | None = None
) -> BotEvent:
    """Point `bot` at `version`. Requires a reason when the bot's account is
    live — a silent config change on real money is the one thing this whole
    area exists to prevent.
    """
    account = session.get(Account, bot.account_id)
    if account is not None and account.mode == Mode.LIVE and not reason:
        raise PresetError(422, "reason required to change a live bot's preset")

    bot.preset_version_id = version.id
    event = BotEvent(
        bot_id=bot.id,
        kind=EventKind.CONFIG_APPLIED,
        message=f"preset assigned: version {version.version}",
        payload_json=json.dumps(
            {"version_id": version.id, "version": version.version, "reason": reason}
        ),
    )
    session.add(event)

    try:
        commands.issue(session, bot, CommandKind.RELOAD_CONFIG, issued_by="system")
    except CommandError:
        # ponytail: a reload_config is already pending — one pull of the new
        # config on the bot's next run covers this assignment too.
        pass

    session.commit()
    session.refresh(event)
    return event
