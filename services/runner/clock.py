"""The clock seam.

Timing is injected so tests are deterministic and nothing waits on real time. Production uses
``SystemClock``; tests use ``FrozenClock`` from ``testing.py``.

The runner must not sleep inside a record's steps -- every wait there is a condition with a
timeout -- but one wait is a *policy* wait rather than a step: holding the tab while a person
decides a commit gate (PL-027). It goes through this seam too, so a test decides instantly.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol


class Clock(Protocol):
    """A source of monotonically increasing time, and a way to pass it."""

    def time(self) -> float:
        """The current time in seconds."""
        ...

    def pause(self, seconds: float) -> None:
        """Let ``seconds`` pass. Tests make this instant."""
        ...


class SystemClock:
    """The real monotonic clock."""

    def time(self) -> float:
        return time.monotonic()

    def pause(self, seconds: float) -> None:
        # One event, waited on once: this is the one place the runner passes real time, and it is
        # here rather than in the run loop so a test can stop it happening at all.
        threading.Event().wait(max(0.0, seconds))
