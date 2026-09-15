"""Configuration loading and paths; credentials never appear in CLI output."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def default_config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "miti" / "config.toml"


@dataclass(frozen=True)
class Settings:
    config_path: Path
    credentials_path: Path
    client_secret_path: Path
    audit_db_path: Path
    forwarding_address: str | None = None
    trusted_senders: frozenset[str] = frozenset()
    trusted_domains: frozenset[str] = frozenset()
    known_vendors: frozenset[str] = frozenset()
    known_contacts: frozenset[str] = frozenset()


def _string_set(data: dict[str, Any], key: str) -> frozenset[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be an array of strings")
    return frozenset(item.lower().strip() for item in value if item.strip())


def load_settings(path: Path | None = None) -> Settings:
    config_path = (path or default_config_path()).expanduser()
    data: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("rb") as stream:
            parsed = tomllib.load(stream)
        if not isinstance(parsed, dict):
            raise ValueError("configuration root must be a TOML table")
        data = parsed
    base = config_path.parent
    def relative(name: str, default: str) -> Path:
        value = data.get(name, default)
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a path string")
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else base / candidate
    forwarding = data.get("forwarding_address")
    if forwarding is not None and not isinstance(forwarding, str):
        raise ValueError("forwarding_address must be a string")
    return Settings(
        config_path=config_path,
        credentials_path=relative("credentials_path", "credentials.json"),
        client_secret_path=relative("client_secret_path", "client_secret.json"),
        audit_db_path=relative("audit_db_path", "audit.sqlite3"),
        forwarding_address=forwarding,
        trusted_senders=_string_set(data, "trusted_senders"),
        trusted_domains=_string_set(data, "trusted_domains"),
        known_vendors=_string_set(data, "known_vendors"),
        known_contacts=_string_set(data, "known_contacts"),
    )
