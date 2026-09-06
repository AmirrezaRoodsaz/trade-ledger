"""App settings loaded from `.env`, plus a status check for venue API keys.

Venue keys (T212_*, OKX_*, KRAKEN_*) are read only for presence-checking in
`env_status()` — they are never modelled as `Settings` fields since adapters
read them directly from the environment by `account.credential_env_prefix`.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DB_PATH: str = "data/ledger.db"
    DATA_DIR: str = "data"
    NOTES_OUT_DIR: str = "notes_out"
    HOST: str = "127.0.0.1"
    PORT: int = 8642


def get_settings() -> Settings:
    return Settings()


def _example_keys() -> list[str]:
    """Key names declared in `.env.example`, comments and blank lines skipped."""
    if not _ENV_EXAMPLE.exists():
        return []
    keys = []
    for line in _ENV_EXAMPLE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0].strip())
    return keys


def env_status() -> dict[str, bool]:
    """Presence (non-empty) of each `.env.example` key. Values are never returned."""
    from_file = dotenv_values(_ENV_FILE) if _ENV_FILE.exists() else {}
    return {key: bool(from_file.get(key) or os.environ.get(key)) for key in _example_keys()}
