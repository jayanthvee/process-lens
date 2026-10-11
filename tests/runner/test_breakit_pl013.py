"""Break-it cases against the PL-013 run orchestrator.

Each test asserts the safe outcome. A failing test is a confirmed way to double-submit or strand a
record.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

import psycopg

from packages.ledger import resume_run, set_run_status
from packages.ledger.models import RecordClaim
from services.runner.executor import ConditionResult, StepCommand, StepResult
from services.runner.reconcile import ReconciledDiff
from services.runner.testing import FakeDestination, FakeExecutor, FakeReconciler, FrozenClock
from services.runner.worker import Worker
from tests.runner.conftest import ORIGIN, Seed, recipe


def _worker(database_url: str, executor: Any, reconciler: Any, worker_id: str) -> Worker:
    return Worker(
        database_url,
        worker_id=worker_id,
        executor=executor,
        reconciler=reconciler,
        clock=FrozenClock(),
        heartbeat_interval=3600.0,
    )


class _LateLandingExecutor(FakeExecutor):
    """Worker A: the click is acknowledged, then lands in the destination only after `in_flight`."""

    def __init__(self, destination: FakeDestination, in_flight: Callable[[], None]) -> None:
        super().__init__(destination)
        self.in_flight = in_flight

    def dispatch(self, command: StepCommand, on_dispatched: Any = None) -> StepResult:
        if on_dispatched is not None:
            on_dispatched()  # the input event is sent; the ledger moves to submitted_unverified
        self.in_flight()  # ...while the save is still on its way to the destination
        ref = self.destination.commit(command.idempotency_key, {"by": "worker-a"})
        return StepResult(status="ok", observed={"ref": ref}, rung=1)


def test_pause_then_resume_during_a_save_never_double_submits(
    seed: Callable[..., Seed], database_url: str
) -> None:
    """An operator pauses and immediately resumes while worker A's save is in flight."""
    base = seed()
    base.add_input_record(key="k1", email="k1@example.com")
    run_id = base.create_run()
    destination = FakeDestination()

    def operator_pauses_and_resumes_while_a_is_saving() -> None:
        with psycopg.connect(database_url, autocommit=True) as op:
            set_run_status(op, run_id, "paused", pause_reason="paused_by_operator")
            resume_run(op, run_id)
        # A second worker picks the resumed run up straight away.
        worker_b = _worker(
            database_url, FakeExecutor(destination), FakeReconciler(destination), "worker-b"
        )
        # B may legitimately be refused; the assertion below decides.
        with contextlib.suppress(Exception):
            worker_b.run_once()

    worker_a = _worker(
        database_url,
        _LateLandingExecutor(destination, operator_pauses_and_resumes_while_a_is_saving),
        FakeReconciler(destination),
        "worker-a",
    )
    # A losing its lease is fine; a second submission is not.
    with contextlib.suppress(Exception):
        worker_a.run_once()

    assert destination.duplicates == 0, (
        "the same key was submitted twice: a resumed run was claimed while its first worker "
        "was still inside the commit window"
    )


SEARCH_THEN_CREATE = [
    {"id": "open", "action": "navigate", "url": f"{ORIGIN}/leads"},
    {
        "id": "search",
        "action": "fill",
        "targets": [{"by": "label", "text": "Search"}],
        "value": {"kind": "input", "column": "email"},
    },
    {
        "id": "exists",
        "action": "branch",
        "when": {"text_visible": "1 result"},
        "then": [
            {"id": "skip", "action": "end_record", "outcome": "skipped", "reason": "already there"}
        ],
    },
    {
        "id": "save",
        "action": "click",
        "commit": True,
        "assert_after": {"text_visible": "Lead created", "timeout_ms": 8000},
        "targets": [{"by": "role", "role": "button", "name": "Save"}],
    },
    {"id": "done", "action": "end_record", "outcome": "verified", "reason": "created"},
]


class _SearchSeesDestination(FakeExecutor):
    """The duplicate search reflects the destination; the save's confirmation never shows."""

    def check_condition(
        self, condition: Any, *, timeout_ms: int, step_id: Any = None, record_key: Any = None
    ) -> ConditionResult:
        if step_id == "exists":
            return ConditionResult(passed=self.destination.search(f"{record_key}") > 0)
        if step_id == "save":
            return ConditionResult(passed=False)  # the confirmation toast is missed
        return ConditionResult(passed=True)


class _LaggingReconciler(FakeReconciler):
    """The destination's search index lags, so the reconcile right after the save finds nothing."""

    def search(self, record: RecordClaim) -> ReconciledDiff:
        return ReconciledDiff(matches=0)


def test_a_retried_record_whose_earlier_save_landed_is_not_stranded(
    seed: Callable[..., Seed], conn: psycopg.Connection, database_url: str
) -> None:
    """Save lands, its confirmation is missed, reconcile lags, the retry's search finds the lead.

    The record is commit-attempted, so `filling -> skipped` is (rightly) refused by the database.
    The runner must still end it safely: verified or parked. Never stuck, never a crash loop,
    never a second save.
    """
    base = seed(recipe(SEARCH_THEN_CREATE, record_key="{{row.key}}", idempotency_key="{{row.key}}"))
    base.add_input_record(key="k1", email="k1@example.com")
    run_id = base.create_run()
    destination = FakeDestination()
    worker = _worker(
        database_url, _SearchSeesDestination(destination), _LaggingReconciler(destination), "w1"
    )

    error: Exception | None = None
    try:
        worker.run_once()
    except Exception as exc:  # noqa: BLE001
        error = exc

    state = conn.execute("SELECT state FROM run_records WHERE run_id = %s", (run_id,)).fetchone()[0]
    assert error is None, f"the worker crashed: {error!r}"
    assert state in {"verified", "parked"}, f"the record was left in {state}"
    assert destination.duplicates == 0
