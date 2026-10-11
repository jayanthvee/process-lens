"""Condition helpers.

Every wait and assertion is a condition with a timeout; a fixed sleep is not expressible in the
recipe format and is not used here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEFAULT_TIMEOUT_MS = 10_000


def condition_timeout(condition: Mapping[str, Any], fallback: int = DEFAULT_TIMEOUT_MS) -> int:
    """The timeout inside a condition, or the fallback when it does not carry one."""
    value = condition.get("timeout_ms")
    return int(value) if isinstance(value, int) else fallback
