"""B2: a refused transition during a record must never crash the worker.

The database guard is the authority on the ledger. When it refuses a write, the worker parks the
record if parking is legal from where it is; otherwise it pauses the run with ``internal_error`` and
logs the details. Either way the worker survives and the record is not stranded.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg
import pytest

from packages.ledger import IllegalTransition, change_state, get_record
from services.runner.run_loop import ALREADY_SAVED_REASON, INTERNAL_ERROR, RunLoop
from services.runner.testing import FakeDestination, FakeExecutor, FakeReconciler, FrozenClock
from services.runner.worker import Worker
from tests.runner.conftest import Seed, recipe


def _worker(database_url: str, destination: FakeDestination, worker_id: str = "w1") -> Worker:
    return Worker(
        database_url,
        worker_id=worker_id,
        executor=FakeExecutor(destination),
        reconciler=FakeReconciler(destination),
        clock=FrozenClock(),
        heartbeat_interval=3600.0,
    )


def _walk(conn: psycopg.Connection, record_id: uuid.UUID, states: list[str]) -> None:
    for state in states:
        conn.execute(
            "UPDATE run_records SET state = %s WHERE id = %s AND state <> %s",
            (state, record_id, state),
        )


# --- the guard refusal is a typed error -------------------------------------


def test_an_illegal_transition_is_a_typed_ledger_error(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    """A caller that catches ``LedgerError`` never sees a raw psycopg exception."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE runs SET status = 'running', worker_id = 'w1', lease_epoch = 1 WHERE id = %s",
            (run_id,),
        )
        cur.execute("SELECT id FROM run_records WHERE run_id = %s", (run_id,))
        record_id = cur.fetchone()[0]
    conn.execute(
        "UPDATE run_records SET state = 'claimed', claimed_by = 'w1' WHERE id = %s", (record_id,)
    )

    # `pending -> verified` is not a legal transition.
    with pytest.raises(IllegalTransition, match="illegal ledger transition"):
        change_state(
            conn,
            record_id,
            worker="w1",
            epoch=1,
            expected="claimed",
            new="verified",
            reason="no",
        )

    # The row is untouched and the connection is still usable.
    record = get_record(conn, record_id)
    assert record is not None and record.state == "claimed"


# --- end_record skipped on a commit-attempted record ------------------------


SEARCH_THEN_CREATE = [
    {"id": "open", "action": "navigate", "url": "http://localhost:5173/leads"},
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
        self,
        condition,
        *,
        timeout_ms,
        step_id=None,
        record_key=None,  # type: ignore[no-untyped-def]
    ):
        from services.runner.executor import ConditionResult

        if step_id == "exists":
            return ConditionResult(passed=self.destination.search(f"{record_key}") > 0)
        if step_id == "save":
            return ConditionResult(passed=False)
        return ConditionResult(passed=True)


class _LaggingReconciler(FakeReconciler):
    """The destination's search index lags, so the reconcile right after the save finds nothing."""

    def search(self, record):  # type: ignore[no-untyped-def]
        from services.runner.reconcile import ReconciledDiff

        return ReconciledDiff(matches=0)


def test_end_record_skipped_on_a_commit_attempted_record_parks_it(
    seed: Callable[..., Seed], conn: psycopg.Connection, database_url: str
) -> None:
    """The recipe's duplicate search finds a record this run may already have saved.

    `filling -> skipped` is refused for a commit-attempted record, so the runner parks it with a
    reason a person can act on. Never a crash, never a second save.
    """
    base = seed(recipe(SEARCH_THEN_CREATE))
    base.add_input_record(key="k1", email="k1@example.com")
    run_id = base.create_run()
    destination = FakeDestination()
    worker = Worker(
        database_url,
        worker_id="w1",
        executor=_SearchSeesDestination(destination),
        reconciler=_LaggingReconciler(destination),
        clock=FrozenClock(),
        heartbeat_interval=3600.0,
    )

    worker.run_once()  # must not raise

    with conn.cursor() as cur:
        cur.execute("SELECT id, state FROM run_records WHERE run_id = %s", (run_id,))
        record_id, state = cur.fetchone()
    assert state == "parked", f"the record was left in {state}"
    record = get_record(conn, record_id)
    assert record is not None
    assert record.commit_attempted is True
    assert ALREADY_SAVED_REASON in (record.reason or "")
    assert destination.duplicates == 0, "nothing was submitted twice"


def test_an_unparkable_refusal_pauses_the_run_internal_error(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    """When parking is not legal from the record's state, the run pauses for a person.

    ``submitted_unverified -> parked`` is not a legal transition, so the generic handler pauses the
    run with ``internal_error`` rather than crashing or stranding the record.
    """
    base = seed()
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM run_records WHERE run_id = %s", (run_id,))
        record_id = cur.fetchone()[0]
    conn.execute(
        "UPDATE runs SET status = 'running', worker_id = 'w1', lease_epoch = 1 WHERE id = %s",
        (run_id,),
    )
    _walk(
        conn,
        record_id,
        ["claimed", "prechecked", "filling", "submitting", "submitted_unverified"],
    )
    conn.execute("UPDATE run_records SET claimed_by = 'w1' WHERE id = %s", (record_id,))

    loop, run, record = _loop_at(conn, run_id, record_id, epoch=1)
    disposition = loop._handle_refused(
        conn,
        record,
        run,
        1,
        IllegalTransition("illegal ledger transition submitted_unverified -> parked"),
    )

    assert disposition is None, "the run pauses when the record cannot be parked"
    with conn.cursor() as cur:
        cur.execute("SELECT status, pause_reason FROM runs WHERE id = %s", (run_id,))
        assert cur.fetchone() == ("paused", INTERNAL_ERROR)
    still = get_record(conn, record_id)
    assert still is not None and still.state == "submitted_unverified", "the record is untouched"


def test_a_refusal_while_parking_is_still_legal_parks_the_record(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    """From `filling` a park is legal, so the generic handler parks rather than pausing the run."""
    base = seed()
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM run_records WHERE run_id = %s", (run_id,))
        record_id = cur.fetchone()[0]
    conn.execute(
        "UPDATE runs SET status = 'running', worker_id = 'w1', lease_epoch = 1 WHERE id = %s",
        (run_id,),
    )
    _walk(conn, record_id, ["claimed", "prechecked", "filling"])
    conn.execute("UPDATE run_records SET claimed_by = 'w1' WHERE id = %s", (record_id,))

    loop, run, record = _loop_at(conn, run_id, record_id, epoch=1)
    disposition = loop._handle_refused(
        conn, record, run, 1, IllegalTransition("the guard refused the write")
    )

    assert disposition == "parked"
    fresh = get_record(conn, record_id)
    assert fresh is not None and fresh.state == "parked"


def test_a_skip_precheck_on_a_commit_attempted_record_parks_it(
    seed: Callable[..., Seed], conn: psycopg.Connection, database_url: str
) -> None:
    """The precheck `skip` branch carries the same B2 rule as `end_record skipped`.

    A record that has already entered the commit window is never reported as skipped; it parks with
    the reason a person can act on.
    """
    base = seed(recipe(rules=[{"when": {"missing": "email"}, "then": "skip", "reason": "handled"}]))
    base.add_input_record(key="k1")
    run_id = base.create_run()

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM run_records WHERE run_id = %s", (run_id,))
        record_id = cur.fetchone()[0]
    _walk(
        conn,
        record_id,
        ["claimed", "prechecked", "filling", "submitting", "reconciling", "pending"],
    )

    destination = FakeDestination()
    worker = Worker(
        database_url,
        worker_id="w1",
        executor=FakeExecutor(destination),
        reconciler=FakeReconciler(destination),
        clock=FrozenClock(),
        heartbeat_interval=3600.0,
    )
    assert worker.run_once() is True

    record = get_record(conn, record_id)
    assert record is not None
    assert record.state == "parked", "a commit-attempted record is never skipped"
    assert ALREADY_SAVED_REASON in (record.reason or "")


def _loop_at(
    conn: psycopg.Connection, run_id: uuid.UUID, record_id: uuid.UUID, *, epoch: int
) -> tuple[RunLoop, object, object]:
    """A RunLoop with its epoch set, as ``process_run`` would leave it, plus the run and record."""
    from packages.ledger import get_run
    from services.runner.run_loop import RunLoop

    destination = FakeDestination()
    loop = RunLoop(
        worker_id="w1",
        executor=FakeExecutor(destination),
        reconciler=FakeReconciler(destination),
        clock=FrozenClock(),
    )
    loop._epoch = epoch
    run = get_run(conn, run_id)
    assert run is not None
    record = get_record(conn, record_id)
    assert record is not None
    return loop, run, record
