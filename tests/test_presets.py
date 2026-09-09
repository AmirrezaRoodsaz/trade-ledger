from __future__ import annotations

from decimal import Decimal

import pytest

from trade_ledger.bots import presets
from trade_ledger.bots.presets import PresetError
from trade_ledger.enums import CommandKind, EventKind, Mode
from trade_ledger.models import BotCommand, BotEvent, Preset, PresetVersion


def _make_preset(session, **overrides) -> Preset:
    preset = presets.create(session, overrides.pop("name", "donchian-4h"), "donchian", None)
    return preset


def _add_version(session, preset, **overrides) -> PresetVersion:
    defaults = {
        "params": {"entry": 55, "exit": 20},
        "timeframe": "4h",
        "pairs": ["BTC/USDT:USDT"],
        "risk_pct": Decimal(3),
        "max_position_pct": Decimal(33),
        "leverage_cap": Decimal(3),
        "note": None,
    }
    defaults.update(overrides)
    return presets.add_version(session, preset, **defaults)


# --- versioning --------------------------------------------------------


def test_versions_number_from_one_and_increment(session):
    preset = _make_preset(session)
    v1 = _add_version(session, preset)
    v2 = _add_version(session, preset)
    assert (v1.version, v2.version) == (1, 2)


def test_two_presets_version_independently(session):
    one = _make_preset(session, name="one")
    two = _make_preset(session, name="two")
    v1 = _add_version(session, one)
    v2 = _add_version(session, two)
    assert v1.version == 1
    assert v2.version == 1


def test_a_version_is_immutable_via_the_api(client, session):
    preset = _make_preset(session)
    version = _add_version(session, preset)
    resp = client.put(
        f"/api/presets/{preset.id}/versions/{version.id}",
        json={"note": "sneaky edit"},
    )
    assert resp.status_code == 405


def test_versions_route_round_trip(client, session):
    preset = _make_preset(session)
    created = client.post(
        f"/api/presets/{preset.id}/versions",
        json={
            "params": {"entry": 55, "exit": 20},
            "timeframe": "4h",
            "pairs": ["BTC/USDT:USDT"],
            "risk_pct": "3",
            "max_position_pct": "33",
            "leverage_cap": "3",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["version"] == 1
    assert body["params"] == {"entry": 55, "exit": 20}

    listed = client.get(f"/api/presets/{preset.id}/versions").json()
    assert [one["version"] for one in listed] == [1]

    fetched = client.get(f"/api/presets/{preset.id}/versions/{body['id']}").json()
    assert fetched == body


# --- assignment ----------------------------------------------------------


def test_assign_to_a_live_bot_without_reason_is_rejected(session, bot_factory, account_factory):
    account = account_factory(mode=Mode.LIVE)
    bot, _ = bot_factory(account_id=account.id)
    preset = _make_preset(session)
    version = _add_version(session, preset)

    with pytest.raises(PresetError) as exc_info:
        presets.assign(session, bot, version, reason=None)
    assert exc_info.value.status_code == 422


def test_assign_to_a_live_bot_with_reason_succeeds(session, bot_factory, account_factory):
    account = account_factory(mode=Mode.LIVE)
    bot, _ = bot_factory(account_id=account.id)
    preset = _make_preset(session)
    version = _add_version(session, preset)

    event = presets.assign(session, bot, version, reason="switching to tighter stops")
    assert bot.preset_version_id == version.id
    assert event.kind == EventKind.CONFIG_APPLIED


def test_assign_to_a_non_live_bot_needs_no_reason(session, bot_factory):
    bot, _ = bot_factory()
    preset = _make_preset(session)
    version = _add_version(session, preset)

    event = presets.assign(session, bot, version, reason=None)
    assert bot.preset_version_id == version.id
    assert event.kind == EventKind.CONFIG_APPLIED


def test_assign_queues_a_reload_config_command(session, bot_factory):
    bot, _ = bot_factory()
    preset = _make_preset(session)
    version = _add_version(session, preset)

    presets.assign(session, bot, version, reason=None)

    pending = (
        session.query(BotCommand).filter_by(bot_id=bot.id, kind=CommandKind.RELOAD_CONFIG).all()
    )
    assert len(pending) == 1
    assert pending[0].issued_by == "system"


def test_assign_skips_queuing_when_a_reload_is_already_pending(session, bot_factory):
    bot, _ = bot_factory()
    preset = _make_preset(session)
    v1 = _add_version(session, preset)
    v2 = _add_version(session, preset)

    presets.assign(session, bot, v1, reason=None)
    presets.assign(session, bot, v2, reason=None)

    pending = (
        session.query(BotCommand).filter_by(bot_id=bot.id, kind=CommandKind.RELOAD_CONFIG).all()
    )
    assert len(pending) == 1
    # The second assignment still applied, only the duplicate command was skipped.
    assert bot.preset_version_id == v2.id


def test_assign_route_rejects_live_without_reason(client, session, bot_factory, account_factory):
    account = account_factory(mode=Mode.LIVE)
    bot, _ = bot_factory(account_id=account.id)
    preset = _make_preset(session)
    version = _add_version(session, preset)

    resp = client.post(f"/api/bots/{bot.slug}/preset", json={"version_id": version.id})
    assert resp.status_code == 422


def test_assign_route_ok(client, session, bot_factory):
    bot, _ = bot_factory()
    preset = _make_preset(session)
    version = _add_version(session, preset)

    resp = client.post(
        f"/api/bots/{bot.slug}/preset",
        json={"version_id": version.id, "reason": None},
    )
    assert resp.status_code == 200, resp.text
    session.expire_all()
    assert bot.preset_version_id == version.id
    assert (
        session.query(BotEvent).filter_by(bot_id=bot.id, kind=EventKind.CONFIG_APPLIED).count() == 1
    )


def test_assign_route_unknown_version_is_404(client, bot_factory):
    bot, _ = bot_factory()
    resp = client.post(f"/api/bots/{bot.slug}/preset", json={"version_id": 999})
    assert resp.status_code == 404
