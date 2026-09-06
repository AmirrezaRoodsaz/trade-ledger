from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from trade_ledger import notes
from trade_ledger.enums import AssetClass, Direction, Mode, TradeStatus
from trade_ledger.models import Playbook, PlaybookVersion, Trade

FIXTURE = Path(__file__).parent / "fixtures" / "notes" / "T-001 BTC long.md"


def _closed_trade(session, account, instrument, **overrides) -> Trade:
    _sentinel = object()
    playbook_version_id = overrides.get("playbook_version_id", _sentinel)
    if playbook_version_id is _sentinel:
        playbook = Playbook(name="Turtle Donchian")
        session.add(playbook)
        session.flush()
        version = PlaybookVersion(playbook_id=playbook.id, version=1, rules_md="breakout")
        session.add(version)
        session.flush()
        playbook_version_id = version.id

    values = {
        "account_id": account.id,
        "instrument_id": instrument.id,
        "direction": Direction.LONG,
        "status": TradeStatus.CLOSED,
        "playbook_version_id": playbook_version_id,
        "planned_entry": Decimal(40000),
        "planned_stop": Decimal(39000),
        "planned_target": Decimal(45000),
        "risk_eur": Decimal(100),
        "planned_qty": Decimal("0.1"),
        "opened_ts": datetime(2026, 8, 1, tzinfo=UTC),
        "closed_ts": datetime(2026, 8, 5, tzinfo=UTC),
        "avg_entry": Decimal(40000),
        "avg_exit": Decimal(42500),
        "quantity": Decimal("0.1"),
        "fees_eur": Decimal(12),
        "result_eur": Decimal(238),
        "adherence": True,
        "note_pre": "Breakout over the 20-day Donchian high, volume confirmed.",
        "note_post": "Held through the pullback per plan, exited at target zone.",
        "tags": json.dumps(["breakout", "btc"]),
        "external_ref": "T-001",
    }
    values.update(overrides)
    trade = Trade(**values)
    session.add(trade)
    session.flush()
    return trade


def test_render_matches_fixture_byte_for_byte(session, account_factory, instrument_factory):
    account = account_factory(name="OKX Paper", mode=Mode.PAPER)
    instrument = instrument_factory(symbol="BTC", asset_class=AssetClass.CRYPTO)
    trade = _closed_trade(session, account, instrument)

    text = notes.render(trade, account, instrument)

    assert text == FIXTURE.read_text()


def test_round_trip_preserves_every_frontmatter_key(session, account_factory, instrument_factory):
    account = account_factory(name="OKX Paper", mode=Mode.PAPER)
    instrument = instrument_factory(symbol="BTC", asset_class=AssetClass.CRYPTO)
    trade = _closed_trade(session, account, instrument)

    parsed = notes.parse(notes.render(trade, account, instrument))

    assert parsed["trade"] is True
    assert parsed["id"] == "T-001"
    assert parsed["mode"] == "paper"
    assert parsed["status"] == "closed"
    assert parsed["venue"] == "OKX Paper"
    assert parsed["market"] == "crypto"
    assert parsed["symbol"] == "BTC"
    assert parsed["direction"] == "long"
    assert parsed["strategy"] == "Turtle Donchian v1"
    assert parsed["opened"].isoformat() == "2026-08-01"
    assert parsed["closed"].isoformat() == "2026-08-05"
    assert parsed["entry"] == Decimal(40000)
    assert parsed["stop"] == Decimal(39000)
    assert parsed["target"] == Decimal(45000)
    assert parsed["quantity"] == Decimal("0.1")
    assert parsed["size_eur"] == Decimal("4000.0")
    assert parsed["fees_eur"] == Decimal(12)
    assert parsed["risk_eur"] == Decimal(100)
    assert parsed["result_eur"] == Decimal(238)
    assert parsed["adherence"] is True
    assert parsed["mistake"] is None
    assert parsed["tags"] == ["breakout", "btc"]
    assert parsed["note_pre"] == "Breakout over the 20-day Donchian high, volume confirmed."
    assert parsed["note_post"] == "Held through the pullback per plan, exited at target zone."


def test_round_trip_with_demo_mode_and_mistake(session, account_factory, instrument_factory):
    """`demo` accounts map to `mode: paper` in the note, and a set mistake and
    an empty tag list both survive the round trip.
    """
    account = account_factory(name="T212 Demo", mode=Mode.DEMO)
    instrument = instrument_factory(symbol="AAPL", asset_class=AssetClass.STOCK)
    trade = _closed_trade(
        session,
        account,
        instrument,
        playbook_version_id=None,
        adherence=False,
        mistake="early_exit",
        tags="[]",
        external_ref="T-002",
    )

    rendered = notes.render(trade, account, instrument)
    assert "mode: paper" in rendered
    assert "tags: []" in rendered
    assert "**Did I follow my own rules?** No — early_exit" in rendered

    parsed = notes.parse(rendered)
    assert parsed["mode"] == "paper"
    assert parsed["mistake"] == "early_exit"
    assert parsed["tags"] == []
    assert parsed["strategy"] is None


def test_export_all_writes_one_file_named_after_the_trade(
    tmp_path, session, account_factory, instrument_factory
):
    account = account_factory(name="OKX Paper", mode=Mode.PAPER)
    instrument = instrument_factory(symbol="BTC", asset_class=AssetClass.CRYPTO)
    _closed_trade(session, account, instrument)

    files = notes.export_all(session, tmp_path)

    assert [f.name for f in files] == ["T-001 BTC long.md"]
    assert files[0].read_text() == FIXTURE.read_text()


def test_round_trip_quotes_yaml_special_characters(session, account_factory, instrument_factory):
    """A colon in the venue name or a `#` in the playbook name would either
    break YAML parsing or silently truncate the value if rendered raw — both
    must come back out of `parse()` exactly as they went in.
    """
    account = account_factory(name="OKX: Paper", mode=Mode.PAPER)
    instrument = instrument_factory(symbol="BTC", asset_class=AssetClass.CRYPTO)
    playbook = Playbook(name="Breakout #1")
    session.add(playbook)
    session.flush()
    version = PlaybookVersion(playbook_id=playbook.id, version=1, rules_md="breakout")
    session.add(version)
    session.flush()
    trade = _closed_trade(session, account, instrument, playbook_version_id=version.id)

    rendered = notes.render(trade, account, instrument)
    parsed = notes.parse(rendered)

    assert parsed["venue"] == "OKX: Paper"
    assert parsed["strategy"] == "Breakout #1 v1"


def test_export_all_filters_by_mode(tmp_path, session, account_factory, instrument_factory):
    paper = account_factory(name="Paper", mode=Mode.PAPER)
    live = account_factory(name="Live", mode="live")
    instrument = instrument_factory(symbol="BTC", asset_class=AssetClass.CRYPTO)
    _closed_trade(session, paper, instrument, external_ref="T-001", playbook_version_id=None)
    _closed_trade(session, live, instrument, external_ref="T-002", playbook_version_id=None)

    files = notes.export_all(session, tmp_path, mode="live")

    assert [f.name for f in files] == ["T-002 BTC long.md"]


def test_export_all_sanitises_slashes_in_the_symbol(
    tmp_path, session, account_factory, instrument_factory
):
    """`instrument.symbol` for an FX pair is `EUR/USD` — `/` in a filename
    component is a path separator, not a character to write.
    """
    account = account_factory(name="Kraken", mode=Mode.PAPER)
    instrument = instrument_factory(symbol="BTC/EUR", asset_class=AssetClass.CRYPTO)
    _closed_trade(session, account, instrument, playbook_version_id=None)

    files = notes.export_all(session, tmp_path)

    assert [f.name for f in files] == ["T-001 BTC-EUR long.md"]
    assert files[0].exists()


def test_import_dir_updates_status_of_existing_trade(
    tmp_path, session, account_factory, instrument_factory
):
    account = account_factory(name="OKX Paper", mode=Mode.PAPER)
    instrument = instrument_factory(symbol="BTC", asset_class=AssetClass.CRYPTO)
    trade = _closed_trade(session, account, instrument, playbook_version_id=None)
    trade.status = TradeStatus.OPEN  # the DB thinks it's still open
    session.commit()

    note = tmp_path / "T-001 BTC long.md"
    note.write_text(FIXTURE.read_text())  # the note (status: closed) is the source of truth

    created, updated, skipped = notes.import_dir(session, tmp_path, account.id)

    assert (created, updated, skipped) == (0, 1, 0)
    session.refresh(trade)
    assert trade.status == TradeStatus.CLOSED
    assert trade.adherence is True


def test_import_dir_creates_a_new_trade_for_an_unknown_ref(
    tmp_path, session, account_factory, instrument_factory
):
    account = account_factory(name="OKX Paper", mode=Mode.PAPER)
    instrument_factory(symbol="BTC", asset_class=AssetClass.CRYPTO)
    note = tmp_path / "T-999 BTC long.md"
    note.write_text(FIXTURE.read_text().replace("id: T-001", "id: T-999"))

    created, updated, skipped = notes.import_dir(session, tmp_path, account.id)

    assert (created, updated, skipped) == (1, 0, 0)
    trade = session.query(Trade).filter(Trade.external_ref == "T-999").one()
    assert trade.status == TradeStatus.CLOSED
    assert trade.account_id == account.id


def test_import_dir_skips_a_new_trade_note_missing_symbol_or_direction(
    tmp_path, session, account_factory
):
    account = account_factory(name="OKX Paper", mode=Mode.PAPER)
    (tmp_path / "T-900 no-symbol.md").write_text(
        FIXTURE.read_text().replace("id: T-001", "id: T-900").replace("symbol: BTC", "symbol: ")
    )
    (tmp_path / "T-901 no-direction.md").write_text(
        FIXTURE.read_text()
        .replace("id: T-001", "id: T-901")
        .replace("direction: long", "direction: ")
    )

    created, updated, skipped = notes.import_dir(session, tmp_path, account.id)

    assert (created, updated, skipped) == (0, 0, 2)
    assert session.query(Trade).count() == 0
