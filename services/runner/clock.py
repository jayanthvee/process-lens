"""The clock seam.

Timing is injected so tests are deterministic and nothing waits on real time. Production uses
``SystemClock``; tests use ``FrozenClock`` from ``testing.py``.
"""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    """A source of monotonically increasing time, in seconds."""

    def time(self) -> float:
        """The current time in seconds."""
        ...


class SystemClock:
    """The real monotonic clock."""

    def time(self) -> float:
        return time.monotonic()
