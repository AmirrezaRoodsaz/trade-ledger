"""CSV importer registry.

Each `importers/<venue>.py` module exposes `parse(data: bytes) -> ImportResult`.
`RowError`/`ImportResult` live here (not in a submodule) because every
importer needs them; submodules import them back with `from . import
RowError, ImportResult` — safe because this module defines both *before* it
imports the submodules below (see `IMPORTERS`).
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module

from pydantic import BaseModel

from ..ledger import TxDraft


class RowError(BaseModel):
    row: int
    reason: str


class ImportResult(BaseModel):
    drafts: list[TxDraft]
    errors: list[RowError]


Importer = Callable[[bytes], ImportResult]

_NAMES = ["trading212", "okx", "kraken", "binance", "generic"]
IMPORTERS: dict[str, Importer] = {
    name: import_module(f".{name}", __name__).parse for name in _NAMES
}
