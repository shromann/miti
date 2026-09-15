"""Runtime storage paths; credentials never appear in CLI output."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def default_storage_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "miti"


@dataclass(frozen=True)
class Settings:
    credentials_path: Path
    client_secret_path: Path
    audit_db_path: Path


def load_settings() -> Settings:
    storage_path = default_storage_path()
    return Settings(
        credentials_path=storage_path / "credentials.json",
        client_secret_path=storage_path / "client_secret.json",
        audit_db_path=storage_path / "audit.sqlite3",
    )
