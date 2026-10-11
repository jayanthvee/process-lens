"""The browser seam.

The runner never touches a page itself. It sends a :class:`StepCommand` to an :class:`Executor`,
which this ticket implements as a scripted fake (``services/runner/testing.py``) and PL-014
implements over the WebSocket hub.

A **commit** step is two messages, not one (R2): :meth:`Executor.prepare` resolves and hit-tests the
target with **no input sent**, while the record is still ``filling``; only after that succeeds
does the runner write ``submitting`` ahead and call :meth:`Executor.dispatch`, which sends the
input.
That split is what keeps a mere locator failure from ever looking like a commit attempt.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

StepStatus = Literal["ok", "rejected", "locator_failed", "timeout", "lost", "not_dispatched"]

OnDispatched = Callable[[], None]


@dataclass(frozen=True)
class StepCommand:
    """One step, with its values already rendered, sent to the executor."""

    command_id: str
    run_id: uuid.UUID
    run_record_id: uuid.UUID
    record_key: str
    idempotency_key: str
    lease_epoch: int
    step: dict[str, Any]
    commit: bool
    expires_at: float


@dataclass(frozen=True)
class StepResult:
    """The executor's answer. ``not_dispatched`` is the only status that proves nothing was sent."""

    status: StepStatus
    observed: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    rung: int | None = None


@dataclass(frozen=True)
class ConditionResult:
    """What a condition check saw."""

    passed: bool
    observed: dict[str, Any] = field(default_factory=dict)


class Executor(Protocol):
    """What the runner needs from the browser side."""

    def prepare(self, command: StepCommand) -> StepResult:
        """Resolve and hit-test the target, sending no input. Used for a commit step (R2)."""
        ...

    def dispatch(
        self, command: StepCommand, on_dispatched: OnDispatched | None = None
    ) -> StepResult:
        """Send the input. ``on_dispatched`` fires once, right after the input event is sent."""
        ...

    def perform(
        self, command: StepCommand, on_dispatched: OnDispatched | None = None
    ) -> StepResult:
        """Run a non-commit step."""
        ...

    def check_condition(
        self,
        condition: dict[str, Any],
        *,
        timeout_ms: int,
        step_id: str | None = None,
        record_key: str | None = None,
    ) -> ConditionResult:
        """Check a page condition, waiting no longer than ``timeout_ms``."""
        ...
