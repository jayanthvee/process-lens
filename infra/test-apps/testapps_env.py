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


# PL-069: OrangeHRM's settings, with the defaults a fresh clone needs.
#
# `seed_orangehrm.py`, `tests/e2e/real_apps.py` and `reset.py` all read through this, so the test
# login lives in one place. `infra/test-apps/.env.example` carries the same values, and a `.env`
# may override any of them. Nothing above reads these keys, so adding them changes no other app.
ORANGEHRM_DEFAULTS: dict[str, str] = {
    "ORANGEHRM_HTTP_PORT": "8070",
    "ORANGEHRM_DB_NAME": "orangehrm_test",
    "ORANGEHRM_DB_USER": "orangehrm",
    "ORANGEHRM_DB_PASSWORD": "orangehrm-test-password",
    "ORANGEHRM_DB_ROOT_PASSWORD": "orangehrm-test-root",
    # The admin login the installer creates. The password must satisfy OrangeHRM's own strength
    # check at install time: upper case, lower case, a digit and a symbol.
    "ORANGEHRM_ADMIN_USER": "Admin",
    "ORANGEHRM_ADMIN_PASSWORD": "ProcessLensTest57!",
    "ORANGEHRM_ADMIN_EMAIL": "admin@processlens.test",
    "ORANGEHRM_ORGANIZATION": "ProcessLens Test",
    "ORANGEHRM_COUNTRY": "US",
    # The people the seeder creates, and the password every one of them signs in with.
    "ORANGEHRM_EMPLOYEE_PASSWORD": "EmployeeTest57!",
}


def orangehrm_settings(env: Mapping[str, str]) -> dict[str, str]:
    """OrangeHRM's settings: the file's own values where present, the defaults everywhere else."""
    settings = dict(ORANGEHRM_DEFAULTS)
    for key in settings:
        value = str(env.get(key, "") or "").strip()
        if value:
            settings[key] = value
    return settings
