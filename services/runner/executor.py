"""The browser seam.

The runner never touches a page itself. It sends a :class:`StepCommand` to an :class:`Executor`,
which this ticket implements as a scripted fake (``services/runner/testing.py``) and PL-014
implements over the WebSocket hub.

A **commit** step is two messages, not one (R2): :meth:`Executor.prepare` resolves and hit-tests the
target with **no input sent**, while the record is still ``filling``; only after that succeeds
does the runner write ``submitting`` ahead and call :meth:`Executor.dispatch`, which sends the
input.
That split is what keeps a mere locator failure from ever looking like a commit attempt.

PL-014 maps these four methods onto the runner↔extension wire
(``services/runner/browser_executor.py`` against ``apps/extension/src/shared/command-protocol.ts``):
``prepare`` → a ``prepare`` message,
``dispatch`` → a ``dispatch`` message with ``commit: true``, ``perform`` → a ``dispatch`` message
with ``commit: false``, and ``check_condition`` → a ``check`` message. The statuses are the wire's,
with ``refused`` added so a guard refusal (a destructive control, an off-origin page) parks the
record rather than failing it. ``probe_confirmation`` is the one call the scripted fake declines.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

StepStatus = Literal[
    "ok", "rejected", "locator_failed", "timeout", "lost", "not_dispatched", "refused"
]

# R1: how a condition check ended. Anything other than ``ok`` means the executor could not answer.
ConditionStatus = Literal["ok", "lost", "refused"]

# PL-014 item 4: the answer to "is the confirmation already on the page?".
#   present     it is there, so a save now could not be verified
#   absent      a real poll found nothing, so the save may go ahead
#   unanswered  the page was asked and did not answer (lost, refused, past the budget)
#   unsupported this executor cannot look at the page at all (the scripted fake)
ConfirmationProbe = Literal["present", "absent", "unanswered", "unsupported"]

OnDispatched = Callable[[], None]


class SnapshotUnavailable(RuntimeError):
    """The page snapshot could not be taken, so no model call may be based on it."""


@dataclass(frozen=True)
class PageSnapshot:
    """The interactive and labelled elements of the page a step acted on.

    ``elements`` is already redacted by the extension: no password field, no typed value and no
    hidden input. ``origin`` is the page the snapshot was taken on, which is also the memory key's
    site.
    """

    origin: str
    elements: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False


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
    # PL-014: the origins the step may act on, from ``meta.allowed_origins``. The extension refuses
    # a command whose page has moved off them.
    allowed_origins: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepResult:
    """The executor's answer. ``not_dispatched`` is the only status that proves nothing was sent."""

    status: StepStatus
    observed: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    rung: int | None = None


@dataclass(frozen=True)
class ConditionResult:
    """What a condition check saw.

    ``passed`` is only meaningful when ``status`` is ``"ok"`` (R1): against a real page a check can
    end with no answer at all — the extension was lost, refused by one of its guards, or never
    replied inside the runner's budget. ``passed=False`` with ``status="ok"`` means one thing: the
    extension polled to its timeout and the condition never held. A no-answer must never be read as
    "the condition is false", because a duplicate-search branch that takes its ``else`` creates a
    record that already exists.
    """

    passed: bool
    observed: dict[str, Any] = field(default_factory=dict)
    status: ConditionStatus = "ok"

    @property
    def answered(self) -> bool:
        """True only when the executor actually polled and reported an answer."""
        return self.status == "ok"


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

    def probe_confirmation(
        self,
        condition: dict[str, Any],
        *,
        step_id: str | None = None,
        record_key: str | None = None,
    ) -> ConfirmationProbe:
        """Is this commit's confirmation already on the page, before the save is sent?

        Only ``"absent"`` lets the save go ahead. ``"present"`` parks (the confirmation could not be
        trusted) and ``"unanswered"`` parks too — a probe nobody answered must never authorise the
        save, because "absent" is the answer that creates the record. ``"unsupported"`` means this
        executor cannot see page state at all, so the gate does not run.
        """
        ...

    def snapshot(self, command: StepCommand) -> PageSnapshot:
        """Return the page's interactive and labelled elements, with no typed values.

        ``command`` supplies the run, record, lease and allowed origins the wire needs. A page that
        cannot be read raises :class:`SnapshotUnavailable`; the runner then parks the row exactly as
        it would without a model, never inventing a target.
        """
        ...
