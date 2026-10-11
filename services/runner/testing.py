"""Fakes for the runner's tests. **Production modules never import this.**

``FakeDestination`` stands in for the downstream system, ``FakeExecutor`` for the browser, and
``CrashHooks`` for a worker dying at one of the four write-ahead points. A test asserts that nothing
under ``services/runner`` outside this file imports it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from packages.ledger import RecordClaim

from .executor import ConditionResult, OnDispatched, StepCommand, StepResult

REJECTED = "rejected"
DUPLICATE = "duplicate"


class FakeDestination:
    """An in-memory destination keyed by idempotency key."""

    def __init__(self, plan: Mapping[str, str] | None = None) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.plan: dict[str, str] = dict(plan or {})
        self.commits = 0
        self.rejects = 0
        self.duplicates = 0

    def commit(self, key: str, payload: Mapping[str, Any] | None = None) -> str:
        """Store one row per key. A second commit of the same key changes nothing."""
        self.commits += 1
        mode = self.plan.get(key, "ok")
        if mode == "rejected":
            self.rejects += 1
            return REJECTED
        if key in self.rows or mode == "duplicate":
            self.duplicates += 1
            return DUPLICATE
        self.rows[key] = dict(payload or {})
        return f"ref:{key}"

    def seed(self, key: str, payload: Mapping[str, Any] | None = None) -> None:
        """Put a row in the destination without counting a commit (a pre-existing record)."""
        self.rows[key] = dict(payload or {})

    def search(self, key: str) -> int:
        return 1 if key in self.rows else 0

    def all_keys(self) -> set[str]:
        return set(self.rows)

    def count_for(self, key: str) -> int:
        return 1 if key in self.rows else 0


class FakeReconciler:
    """A ``Reconciler`` driven by the fake destination, with scripted ambiguity and errors."""

    def __init__(
        self,
        destination: FakeDestination,
        *,
        ambiguous: Iterable[str] = (),
        errors: Iterable[str] = (),
        matches: Mapping[str, int] | None = None,
    ) -> None:
        self.destination = destination
        self.ambiguous = set(ambiguous)
        self.errors = set(errors)
        self.matches = dict(matches or {})

    def search(self, record: RecordClaim) -> Any:
        from .reconcile import ReconciledDiff

        key = record.idempotency_key
        if key in self.errors:
            return ReconciledDiff(matches=0, error="the destination search failed")
        if key in self.ambiguous:
            return ReconciledDiff(matches=2)
        if key in self.matches:
            count = self.matches[key]
            return ReconciledDiff(
                matches=count, destination_ref=f"ref:{key}" if count == 1 else None
            )
        count = self.destination.search(key)
        return ReconciledDiff(matches=count, destination_ref=f"ref:{key}" if count == 1 else None)


class FakeExecutor:
    """A scriptable executor that drives a :class:`FakeDestination` on a commit.

    Plan keys are either a step id (``"save-lead"``) or ``"<record_key>:<step_id>"``, for a
    per-record override. Behaviors: ``ok``, ``locator_failure``, ``rejected``, ``timeout``,
    ``failure``, ``lost_dispatch`` (dispatch reports ``not_dispatched``). Conditions default to
    True and are keyed the same way.
    """

    def __init__(
        self,
        destination: FakeDestination,
        *,
        plan: Mapping[str, str] | None = None,
        conditions: Mapping[str, bool] | None = None,
        lost: Iterable[str] = (),
    ) -> None:
        self.destination = destination
        self.plan = dict(plan or {})
        self.conditions = dict(conditions or {})
        self.lost = set(lost)
        self.commands: list[StepCommand] = []
        self.prepared: list[StepCommand] = []

    def _behavior(self, command: StepCommand) -> str:
        step_id = str(command.step.get("id", ""))
        return self.plan.get(f"{command.record_key}:{step_id}", self.plan.get(step_id, "ok"))

    def _condition(self, step_id: str | None, record_key: str | None, default: bool = True) -> bool:
        if step_id is None:
            return default
        if record_key is not None:
            key = f"{record_key}:{step_id}"
            if key in self.conditions:
                return self.conditions[key]
        return self.conditions.get(step_id, default)

    def prepare(self, command: StepCommand) -> StepResult:
        self.commands.append(command)
        self.prepared.append(command)
        if self._behavior(command) == "locator_failure":
            return StepResult(status="locator_failed", error="the target could not be resolved")
        return StepResult(status="ok", rung=1)

    def dispatch(
        self, command: StepCommand, on_dispatched: OnDispatched | None = None
    ) -> StepResult:
        behavior = self._behavior(command)
        if behavior == "lost_dispatch" or command.command_id in self.lost:
            return StepResult(status="not_dispatched", error="the command was not dispatched")
        if behavior == "rejected_no_ack":
            # A rejection reported without ever firing the acknowledgement callback.
            return StepResult(status="rejected", error="the destination rejected the change")
        if behavior == "rejected":
            # The destination rejects the change after the acknowledgement.
            if on_dispatched is not None:
                on_dispatched()
            return StepResult(status="rejected", error="the destination rejected the change")
        if behavior == "no_commit":
            # Acknowledged, but the destination never received the change (reconcile finds none).
            if on_dispatched is not None:
                on_dispatched()
            return StepResult(status="ok", observed={"ref": None}, rung=1)

        result = self.destination.commit(
            command.idempotency_key,
            {"record_key": command.record_key, "step": command.step.get("id")},
        )
        if on_dispatched is not None:
            on_dispatched()
        if result == REJECTED:
            return StepResult(
                status="rejected", error="the destination rejected the change", observed={}
            )
        ref = None if result == DUPLICATE else result
        return StepResult(status="ok", observed={"ref": ref}, rung=1)

    def perform(
        self, command: StepCommand, on_dispatched: OnDispatched | None = None
    ) -> StepResult:
        self.commands.append(command)
        behavior = self._behavior(command)
        if behavior in ("failure", "locator_failure"):
            return StepResult(status="locator_failed", error="the target could not be resolved")
        if behavior == "rejected":
            return StepResult(status="rejected", error="the destination rejected the change")
        if behavior == "timeout":
            return StepResult(status="timeout", error="the step timed out")
        return StepResult(status="ok", rung=1)

    def check_condition(
        self,
        condition: Mapping[str, Any],
        *,
        timeout_ms: int,
        step_id: str | None = None,
        record_key: str | None = None,
    ) -> ConditionResult:
        return ConditionResult(passed=self._condition(step_id, record_key))


class SimulatedCrash(RuntimeError):
    """A worker dying at a write-ahead point."""

    def __init__(self, point: str) -> None:
        super().__init__(f"simulated crash at {point}")
        self.point = point


class CrashHooks:
    """Fire :class:`SimulatedCrash` at the named write-ahead points."""

    def __init__(self, points: Iterable[str]) -> None:
        self.points = set(points)
        self.fired: list[str] = []

    def __call__(self, point: str) -> None:
        if point in self.points:
            self.fired.append(point)
            raise SimulatedCrash(point)


class FrozenClock:
    """A clock the test moves by hand."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@dataclass
class ScriptedRun:
    """A bundle of the fakes a runner test needs."""

    destination: FakeDestination
    executor: FakeExecutor
    reconciler: FakeReconciler
    clock: FrozenClock
    crash_hooks: CrashHooks | None = None

    @classmethod
    def build(
        cls,
        *,
        plan: Mapping[str, str] | None = None,
        conditions: Mapping[str, bool] | None = None,
        destination_plan: Mapping[str, str] | None = None,
        ambiguous: Iterable[str] = (),
        errors: Iterable[str] = (),
        crash_at: Iterable[str] = (),
    ) -> ScriptedRun:
        destination = FakeDestination(destination_plan)
        return cls(
            destination=destination,
            executor=FakeExecutor(destination, plan=plan, conditions=conditions),
            reconciler=FakeReconciler(destination, ambiguous=ambiguous, errors=errors),
            clock=FrozenClock(),
            crash_hooks=CrashHooks(crash_at) if crash_at else None,
        )


__all__ = [
    "DUPLICATE",
    "REJECTED",
    "CrashHooks",
    "FakeDestination",
    "FakeExecutor",
    "FakeReconciler",
    "FrozenClock",
    "ScriptedRun",
    "SimulatedCrash",
]
