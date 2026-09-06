"""Small helpers shared by the CSV importers: decoding, number parsing, and
date parsing that accepts whatever format a given venue's export uses.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from dateutil import parser as _date_parser


def read_rows(data: bytes) -> csv.DictReader:
    """`utf-8-sig` strips a BOM if present, which several brokers emit."""
    return csv.DictReader(io.StringIO(data.decode("utf-8-sig")))


def parse_utc(value: str) -> datetime:
    """Parse a date/time string; naive results are assumed UTC (every venue
    here reports UTC without an offset).
    """
    dt = _date_parser.parse(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def dec(value: str | None) -> Decimal:
    """Parse a CSV cell into a `Decimal`, treating blank as zero. Raises
    `ValueError` (not `InvalidOperation`) so importers can catch one type
    alongside `KeyError`/`ValueError` for malformed rows.
    """
    if value is None:
        return Decimal(0)
    value = value.strip().replace(",", "")
    if value == "":
        return Decimal(0)
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid number: {value!r}") from exc
