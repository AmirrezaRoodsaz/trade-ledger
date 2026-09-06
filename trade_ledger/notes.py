"""Obsidian note export/import for `Trade` rows.

Mirrors the sister vault's `_Trade Template.md` frontmatter exactly — same
keys, same order — so its `trade_stats.py` script can read a note this
module writes without any changes on that side. `render()` is hand-built
string formatting rather than `yaml.dump`: the template leaves empty
scalars blank (`opened: `), which no YAML dumper produces by default.
`parse()` reads the frontmatter back with `yaml.safe_load` (blank/quoted
values are valid YAML either way) plus a couple of regexes for the two body
sections.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from .enums import AssetClass, Mode, TradeStatus
from .ledger import get_or_create_instrument
from .models import Account, Instrument, Playbook, PlaybookVersion, Trade

FRONTMATTER_KEYS = (
    "trade",
    "id",
    "mode",
    "status",
    "venue",
    "market",
    "symbol",
    "direction",
    "strategy",
    "opened",
    "closed",
    "entry",
    "stop",
    "target",
    "quantity",
    "size_eur",
    "fees_eur",
    "risk_eur",
    "result_eur",
    "adherence",
    "mistake",
    "tags",
)

# market = asset_class, mapped 1:1 except perp (the template has no separate
# perp market) which folds into cfd.
_MARKET_BY_ASSET_CLASS = {
    AssetClass.CRYPTO: "crypto",
    AssetClass.STOCK: "stock",
    AssetClass.ETF: "etf",
    AssetClass.FX: "fx",
    AssetClass.CFD: "cfd",
    AssetClass.PERP: "cfd",
}
_ASSET_CLASS_BY_MARKET = {"crypto": AssetClass.CRYPTO, "stock": AssetClass.STOCK,
                          "etf": AssetClass.ETF, "fx": AssetClass.FX, "cfd": AssetClass.CFD}

_MODE_BY_ACCOUNT_MODE = {Mode.LIVE: "live", Mode.PAPER: "paper", Mode.DEMO: "paper"}

_FRONTMATTER_DECIMAL_KEYS = (
    "entry", "stop", "target", "quantity", "size_eur", "fees_eur", "risk_eur", "result_eur",
)


def _decimal_str(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def _date_str(value: datetime | None) -> str:
    return "" if value is None else value.date().isoformat()


def _blank_or(value: str) -> str:
    """Template convention: an empty text field renders as `""`, not blank."""
    return value if value else '""'


def _strategy_label(trade: Trade) -> str:
    if trade.playbook_version_id is None:
        return ""
    session = object_session(trade)
    row = session.execute(
        select(Playbook.name, PlaybookVersion.version)
        .join(PlaybookVersion, PlaybookVersion.playbook_id == Playbook.id)
        .where(PlaybookVersion.id == trade.playbook_version_id)
    ).first()
    return f"{row[0]} v{row[1]}" if row else ""


def render(trade: Trade, account: Account, instrument: Instrument) -> str:
    """The trade as a fully-filled `_Trade Template` note (frontmatter + body)."""
    # `entry`/`quantity` show the realised fill once the trade has one,
    # otherwise the plan — the same fields the template asks for before the
    # order is placed. `stop`/`target` have no "realised" counterpart, so
    # they are always the planned value.
    entry = trade.avg_entry if trade.avg_entry is not None else trade.planned_entry
    quantity = trade.quantity if trade.quantity is not None else trade.planned_qty
    size_eur = entry * quantity if entry is not None and quantity is not None else None

    if trade.adherence is None:
        adherence = ""
    else:
        adherence = "true" if trade.adherence else "false"

    fields = {
        "trade": "true",
        "id": trade.external_ref or "",
        "mode": _MODE_BY_ACCOUNT_MODE[Mode(account.mode)],
        "status": str(trade.status),
        "venue": account.name,
        "market": _MARKET_BY_ASSET_CLASS[AssetClass(instrument.asset_class)],
        "symbol": instrument.symbol,
        "direction": str(trade.direction),
        "strategy": _blank_or(_strategy_label(trade)),
        "opened": _date_str(trade.opened_ts),
        "closed": _date_str(trade.closed_ts),
        "entry": _decimal_str(entry),
        "stop": _decimal_str(trade.planned_stop),
        "target": _decimal_str(trade.planned_target),
        "quantity": _decimal_str(quantity),
        "size_eur": _decimal_str(size_eur),
        "fees_eur": _decimal_str(trade.fees_eur),
        "risk_eur": _decimal_str(trade.risk_eur),
        "result_eur": _decimal_str(trade.result_eur),
        "adherence": adherence,
        "mistake": _blank_or(trade.mistake or ""),
    }

    lines = ["---"]
    for key in FRONTMATTER_KEYS:
        if key == "tags":
            continue
        lines.append(f"{key}: {fields[key]}")
    tags = json.loads(trade.tags or "[]")
    if tags:
        lines.append("tags:")
        lines.extend(f"  - {tag}" for tag in tags)
    else:
        lines.append("tags: []")
    lines.append("---")

    if trade.adherence is None:
        did_i_follow = "**Did I follow my own rules?** "
    elif trade.adherence:
        did_i_follow = "**Did I follow my own rules?** Yes"
    else:
        suffix = f" — {trade.mistake}" if trade.mistake else ""
        did_i_follow = f"**Did I follow my own rules?** No{suffix}"

    lines += [
        "",
        f"# {trade.external_ref} — {instrument.symbol} {trade.direction}",
        "",
        "## Before the trade",
        "",
        trade.note_pre or "",
        "",
        "## After the trade",
        "",
        trade.note_post or "",
        "",
        did_i_follow,
    ]
    return "\n".join(lines) + "\n"


def export_all(session: Session, out_dir: str | Path, mode: str | None = None) -> list[Path]:
    """Write one note per trade to `out_dir`, overwriting existing files.
    `mode` filters to that account mode (`None`/`"all"` exports every trade).
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    stmt = select(Trade)
    if mode not in (None, "all"):
        stmt = stmt.where(Trade.account_id.in_(select(Account.id).where(Account.mode == mode)))

    written: list[Path] = []
    for trade in session.execute(stmt).scalars():
        account = session.get(Account, trade.account_id)
        instrument = session.get(Instrument, trade.instrument_id)
        ref = trade.external_ref or f"trade-{trade.id}"
        path = out / f"{ref} {instrument.symbol} {trade.direction}.md"
        path.write_text(render(trade, account, instrument))
        written.append(path)
    return written


def _split_frontmatter(text: str) -> tuple[str, str]:
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("no frontmatter block found")
    return parts[1], parts[2]


def _parse_body(body: str) -> tuple[str | None, str | None]:
    before = re.search(r"## Before the trade\n\n(.*?)\n\n## After the trade", body, re.DOTALL)
    after = re.search(r"## After the trade\n\n(.*?)\n\n\*\*Did I follow my own rules", body, re.DOTALL)
    note_pre = before.group(1).strip() if before else None
    note_post = after.group(1).strip() if after else None
    return (note_pre or None), (note_post or None)


def parse(text: str) -> dict:
    """Frontmatter keys plus `note_pre`/`note_post`, typed (`Decimal` for the
    money/quantity fields, `date` for `opened`/`closed`, `list[str]` for tags).
    """
    fm_text, body = _split_frontmatter(text)
    raw = yaml.safe_load(fm_text) or {}
    data = dict(raw)

    for key in _FRONTMATTER_DECIMAL_KEYS:
        value = data.get(key)
        data[key] = None if value in (None, "") else Decimal(str(value))

    for key in ("opened", "closed"):
        value = data.get(key)
        data[key] = date.fromisoformat(str(value)) if value else None

    data["tags"] = data.get("tags") or []
    data["mistake"] = data.get("mistake") or None
    data["strategy"] = data.get("strategy") or None

    note_pre, note_post = _parse_body(body)
    data["note_pre"] = note_pre
    data["note_post"] = note_post
    return data


def _apply_parsed(trade: Trade, data: dict) -> None:
    """Update the fields a note can actually change. `avg_entry`/`avg_exit`
    are derived by `journal.recompute` from linked fills and are left alone
    here even though the note's `entry` overlaps with `avg_entry` once a
    trade has fills — only a still-`planned` trade takes `entry`/`quantity`
    straight from the note.
    """
    if data.get("status") in set(TradeStatus):
        trade.status = data["status"]
    trade.adherence = data.get("adherence")
    trade.mistake = data.get("mistake")
    trade.note_pre = data.get("note_pre")
    trade.note_post = data.get("note_post")
    trade.tags = json.dumps(data.get("tags") or [])
    trade.planned_stop = data.get("stop")
    trade.planned_target = data.get("target")
    trade.risk_eur = data.get("risk_eur")
    trade.result_eur = data.get("result_eur")
    trade.fees_eur = data.get("fees_eur")
    if data.get("opened"):
        trade.opened_ts = datetime.combine(data["opened"], datetime.min.time(), tzinfo=UTC)
    if data.get("closed"):
        trade.closed_ts = datetime.combine(data["closed"], datetime.min.time(), tzinfo=UTC)
    if trade.status == TradeStatus.PLANNED:
        trade.planned_entry = data.get("entry")
        trade.planned_qty = data.get("quantity")


def import_dir(session: Session, dir: str | Path, account_id: int) -> tuple[int, int]:
    """Read every `*.md` note in `dir`, matching on `external_ref` (the
    note's `id`). Creates a new (planned-shaped) trade for an id not already
    in the DB, otherwise updates the existing one. Returns `(created, updated)`.
    """
    created = updated = 0
    for path in sorted(Path(dir).glob("*.md")):
        data = parse(path.read_text())
        ref = data.get("id")
        if not ref:
            continue

        trade = session.execute(
            select(Trade).where(Trade.external_ref == ref)
        ).scalar_one_or_none()

        if trade is None:
            asset_class = _ASSET_CLASS_BY_MARKET.get(data.get("market"), AssetClass.CRYPTO)
            instrument = get_or_create_instrument(session, data["symbol"], asset_class)
            trade = Trade(
                account_id=account_id,
                instrument_id=instrument.id,
                direction=data["direction"],
                external_ref=ref,
                status=data.get("status") or TradeStatus.PLANNED,
            )
            session.add(trade)
            created += 1
        else:
            updated += 1

        _apply_parsed(trade, data)

    session.commit()
    return created, updated
