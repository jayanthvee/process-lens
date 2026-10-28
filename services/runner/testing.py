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

from .executor import ConditionResult, ConfirmationProbe, OnDispatched, StepCommand, StepResult

REJECTED = "rejected"
DUPLICATE = "duplicate"

# PL-097: the extension's hit-test diagnosis for a click something covered. The run-loop test uses
# this exact message, and the record's reason must not be it.
NOT_CLICKABLE_ERROR = (
    "the click target could not be resolved: the target is not clickable: "
    "the box center is covered by <div>"
)


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
    per-record override. Behaviors: ``ok``, ``locator_failure``, ``not_clickable``, ``rejected``,
    ``timeout``, ``failure``, ``lost_dispatch`` (dispatch reports ``not_dispatched``), ``refused``
    (a guard refused it before any input). Conditions default to True and are keyed the same way.

    ``probe_confirmation`` returns ``"unsupported"`` (PL-014 R1): a scripted fake has no page, so it
    can never report a confirmation that is already on screen. Reporting "unsupported" rather than
    "absent" is what keeps the fresh-confirmation gate from changing what any existing test means.
    A test that scripts ``probe_page`` is modelling a page that does answer: the probe then
    evaluates the condition against the predicates that page states hold before the step.
    """

    def __init__(
        self,
        destination: FakeDestination,
        *,
        plan: Mapping[str, str] | None = None,
        conditions: Mapping[str, bool] | None = None,
        lost: Iterable[str] = (),
        condition_statuses: Mapping[str, str] | None = None,
        condition_observed: Mapping[str, dict[str, Any]] | None = None,
        probe_page: Mapping[str, Mapping[str, bool]] | None = None,
        confirmation: ConfirmationProbe = "unsupported",
    ) -> None:
        self.destination = destination
        self.plan = dict(plan or {})
        self.conditions = dict(conditions or {})
        self.lost = set(lost)
        self.condition_statuses = dict(condition_statuses or {})
        self.condition_observed = dict(condition_observed or {})
        # PL-118 review B1: a real page answers the freshness probe by evaluating the condition.
        # When a test scripts the page, the probe answers from here instead of ``confirmation``.
        self.probe_page = None if probe_page is None else dict(probe_page)
        self.confirmation = confirmation
        self.commands: list[StepCommand] = []
        self.prepared: list[StepCommand] = []
        self.probes: list[tuple[str | None, str | None]] = []
        # PL-027: the abandon guard's two calls, and what a release reports having refused.
        self.guard_calls: list[str] = []
        self.guard_blocked: list[dict[str, str]] = []
        # PL-102: what each ask for an emailed code answers, in order, and how many were asked.
        # With nothing scripted the person has not said yes, so the run pauses as it always did.
        self.code_answers: list[dict[str, Any]] = []
        self.code_requests = 0

    def guard(self, action: str) -> list[dict[str, str]]:
        """Record the guard call; a release reports whatever the test scripted as refused."""
        self.guard_calls.append(action)
        return list(self.guard_blocked) if action == "disarm" else []

    def code_from_email(self) -> dict[str, Any]:
        """PL-102: the scripted answer to reading the code from the person's email."""
        self.code_requests += 1
        if self.code_answers:
            return self.code_answers.pop(0)
        return {"entered": False, "reason": "not_allowed"}

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
        if self._behavior(command) == "not_clickable":
            return StepResult(status="locator_failed", error=NOT_CLICKABLE_ERROR)
        if self._behavior(command) == "locator_failure":
            return StepResult(status="locator_failed", error="the target could not be resolved")
        return StepResult(status="ok", rung=1)

    def dispatch(
        self, command: StepCommand, on_dispatched: OnDispatched | None = None
    ) -> StepResult:
        behavior = self._behavior(command)
        if behavior == "lost_dispatch" or command.command_id in self.lost:
            return StepResult(status="not_dispatched", error="the command was not dispatched")
        if behavior == "refused":
            # A guard refused it before any input: an off-origin page, a destructive control.
            return StepResult(status="refused", error="a guard refused the command")
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
        if behavior == "not_clickable":
            return StepResult(status="locator_failed", error=NOT_CLICKABLE_ERROR)
        if behavior in ("failure", "locator_failure"):
            return StepResult(status="locator_failed", error="the target could not be resolved")
        if behavior == "refused":
            return StepResult(status="refused", error="a guard refused the command")
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
        status = self._status(step_id, record_key)
        if status != "ok":
            return ConditionResult(passed=False, status=status)  # type: ignore[arg-type]
        return ConditionResult(
            passed=self._condition(step_id, record_key),
            observed=self._observed(step_id, record_key),
        )

    def _observed(self, step_id: str | None, record_key: str | None) -> dict[str, Any]:
        """What the page showed with this condition, scripted per step (PL-118)."""
        if step_id is None:
            return {}
        if record_key is not None:
            key = f"{record_key}:{step_id}"
            if key in self.condition_observed:
                return dict(self.condition_observed[key])
        return dict(self.condition_observed.get(step_id, {}))

    def _status(self, step_id: str | None, record_key: str | None) -> str:
        """The scripted answer status (R1 tests use ``lost`` and ``refused``)."""
        if step_id is not None and record_key is not None:
            key = f"{record_key}:{step_id}"
            if key in self.condition_statuses:
                return self.condition_statuses[key]
        if step_id is not None and step_id in self.condition_statuses:
            return self.condition_statuses[step_id]
        return "ok"

    def probe_confirmation(
        self,
        condition: Mapping[str, Any],
        *,
        step_id: str | None = None,
        record_key: str | None = None,
    ) -> ConfirmationProbe:
        self.probes.append((step_id, record_key))
        if self.probe_page is not None:
            return "present" if self._page_satisfies(condition, step_id, record_key) else "absent"
        return self.confirmation

    def _page_satisfies(
        self, condition: Mapping[str, Any], step_id: str | None, record_key: str | None
    ) -> bool:
        """Whether the scripted page already satisfies the condition, predicate by predicate.

        A real executor evaluates the condition against the page; the scripted page states, for
        each predicate the condition may carry, whether it holds *before* the step. A predicate
        the test did not state is false, so a condition can never pass by omission.
        """
        if step_id is None:
            return False
        page = self.probe_page.get(step_id)
        if page is None and record_key is not None:
            page = self.probe_page.get(f"{record_key}:{step_id}")
        if page is None:
            return False
        predicates = [key for key in condition if key != "timeout_ms"]
        if not predicates:
            return False
        return all(page.get(key) is True for key in predicates)


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
    """A clock the test moves by hand.

    ``pause`` advances it instantly, so a wait the run loop makes against this clock costs nothing
    and ends the moment the test has moved it past the deadline.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def pause(self, seconds: float) -> None:
        """Let the frozen clock move, without waiting for real time."""
        self.advance(seconds)


class EventDrain:
    """A scripted run-level event queue (PL-014 R6).

    The run loop drains through a callable, so a test can inject any sequence, including one that
    arrives *between* a dispatch and its result.
    """

    def __init__(self, events: Iterable[tuple[str, str]] = ()) -> None:
        self.pending: list[tuple[str, str]] = list(events)

    def __call__(self) -> list[tuple[str, str]]:
        drained, self.pending = self.pending, []
        return drained

    def push(self, kind: str, detail: str = "") -> None:
        self.pending.append((kind, detail))


@dataclass
class ScriptedRun:
    """A bundle of the fakes a runner test needs."""

    destination: FakeDestination
    executor: FakeExecutor
    reconciler: FakeReconciler
    clock: FrozenClock
    crash_hooks: CrashHooks | None = None
    events: EventDrain | None = None

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
        condition_statuses: Mapping[str, str] | None = None,
        condition_observed: Mapping[str, dict[str, Any]] | None = None,
        probe_page: Mapping[str, Mapping[str, bool]] | None = None,
        confirmation: ConfirmationProbe = "unsupported",
        events: Iterable[tuple[str, str]] = (),
    ) -> ScriptedRun:
        destination = FakeDestination(destination_plan)
        return cls(
            destination=destination,
            executor=FakeExecutor(
                destination,
                plan=plan,
                conditions=conditions,
                condition_statuses=condition_statuses,
                condition_observed=condition_observed,
                probe_page=probe_page,
                confirmation=confirmation,
            ),
            reconciler=FakeReconciler(destination, ambiguous=ambiguous, errors=errors),
            clock=FrozenClock(),
            crash_hooks=CrashHooks(crash_at) if crash_at else None,
            events=EventDrain(events),
        )


__all__ = [
    "DUPLICATE",
    "NOT_CLICKABLE_ERROR",
    "REJECTED",
    "CrashHooks",
    "EventDrain",
    "FakeDestination",
    "FakeExecutor",
    "FakeReconciler",
    "FrozenClock",
    "ScriptedRun",
    "SimulatedCrash",
]
