"""App settings loaded from `.env`, plus a status check for venue API keys.

Venue keys (T212_*, OKX_*, KRAKEN_*) are read only for presence-checking in
`env_status()` — they are never modelled as `Settings` fields since adapters
read them directly from the environment by `account.credential_env_prefix`.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    # The same `.env` `env_status()` reports on — an absolute path, so the
    # app reads one file whichever directory it was started from.
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    DB_PATH: str = "data/ledger.db"
    DATA_DIR: str = "data"
    NOTES_OUT_DIR: str = "notes_out"
    HOST: str = "127.0.0.1"
    PORT: int = 8642

    @field_validator("DB_PATH", "DATA_DIR")
    @classmethod
    def _under_repo_root(cls, value: str) -> str:
        """A relative `DB_PATH`/`DATA_DIR` is relative to the repo root, not
        the cwd: `uv run trade-ledger` from a subdirectory must still find the
        one database and the one screenshots directory. `":memory:"` is
        SQLite's in-memory marker, not a path — left alone.
        """
        path = Path(value)
        if value == ":memory:" or path.is_absolute():
            return value
        return str(_REPO_ROOT / path)


def get_settings() -> Settings:
    return Settings()


def _parse_env_file(path: Path) -> dict[str, str]:
    """Tiny stdlib `key=value` parser — comments and blank lines skipped."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _example_keys() -> list[str]:
    """Key names declared in `.env.example`."""
    return list(_parse_env_file(_ENV_EXAMPLE))


def env_status() -> dict[str, bool]:
    """Presence (non-empty) of each `.env.example` key. Values are never returned."""
    from_file = _parse_env_file(_ENV_FILE)
    return {key: bool(from_file.get(key) or os.environ.get(key)) for key in _example_keys()}


def env_values() -> dict[str, str]:
    """All `key=value` pairs from `.env`. For callers (adapter `credentials()`)
    that need the actual values rather than presence — they own not leaking
    them further.
    """
    return _parse_env_file(_ENV_FILE)
