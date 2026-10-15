"""Settings for the local test apps, in one place.

`reset.py` and both seeders read their configuration through here. The file to
read is `.env` when it exists, otherwise the committed `.env.example`, so a fresh
clone works with no setup. Only keys the file already defines are taken from the
real environment, so unrelated environment variables never leak into a seeder.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
EXAMPLE_FILE = HERE / ".env.example"


def env_file_in_use() -> Path:
    """The file `load_env` will read: `.env` if present, else `.env.example`."""
    return ENV_FILE if ENV_FILE.is_file() else EXAMPLE_FILE


def parse_env_file(text: str) -> dict[str, str]:
    """Parse the simple `KEY=value` form the env files use."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def load_env(path: Path | None = None) -> dict[str, str]:
    """Load the test-app settings, letting the real environment win per key."""
    source = Path(path) if path is not None else env_file_in_use()
    values = parse_env_file(source.read_text(encoding="utf-8"))
    values.update({key: os.environ[key] for key in values if key in os.environ})
    return values


def setting(env: Mapping[str, str], key: str, default: str | None = None) -> str:
    """Read one setting, falling back to `default` for missing or empty values."""
    value = env.get(key, "")
    if value:
        return value
    if default is None:
        raise KeyError(f"{key} is missing from {env_file_in_use().name}")
    return default
