"""Shared API types: `Decimal` fields that serialise as JSON strings, aware
datetimes, and a generic `{items, total}` page envelope.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, ConfigDict, PlainSerializer
from pydantic import BaseModel as PydanticBaseModel

Money = Annotated[Decimal, PlainSerializer(str, return_type=str)]


def to_utc(value: datetime) -> datetime:
    """A datetime with no offset is read as UTC rather than rejected.

    The `UTCDateTime` column refuses naive values, so without this a client
    sending `2026-03-02T10:00:00` gets a 500 out of SQLAlchemy instead of an
    answer. Everything the app stores is UTC anyway.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


UTCDatetime = Annotated[datetime, AfterValidator(to_utc)]


class BaseModel(PydanticBaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](PydanticBaseModel):
    items: list[T]
    total: int
