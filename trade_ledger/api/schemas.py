"""Shared API types: `Decimal` fields that serialise as JSON strings, and a
generic `{items, total}` page envelope.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, PlainSerializer

Money = Annotated[Decimal, PlainSerializer(str, return_type=str)]


class BaseModel(PydanticBaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](PydanticBaseModel):
    items: list[T]
    total: int
