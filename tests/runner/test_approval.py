"""Approvals: the policy gate holds a run at the commit step, and a person's decision runs it.

PL-027 changed what "paused for approval" means. The record used to be RELEASED and re-queued, and
the next claim re-ran its recipe from step 1; on a real app that restart navigates away from the
half-filled form and the destination autosaves it, so a record reached the destination with no
commit step and no approval. Now the record holds the tab AT the commit step: approving executes
the commit that is already prepared, and the run's own rows are asserted through the ledger.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable

import psycopg

from packages.ledger import get_record, record_approval, resume_run
from services.runner.clock import SystemClock
from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed, recipe


def _user(conn: psycopg.Connection, tenant_id: uuid.UUID) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (tenant_id, email, name, role) "
            "VALUES (%s, %s, 'Reviewer', 'reviewer') RETURNING id",
            (tenant_id, f"reviewer-{uuid.uuid4().hex[:6]}@example.com"),
        )
        return cur.fetchone()[0]


def _requests(conn: psycopg.Connection, run_id: uuid.UUID) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, run_record_id, step_id, shown, resolved_by_approval_id "
            "FROM approval_requests WHERE run_id = %s ORDER BY created_at, id",
            (run_id,),
        )
        columns = ("id", "run_record_id", "step_id", "shown", "resolved_by_approval_id")
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def _decide(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    decision: str,
    *,
    reason: str | None = None,
) -> uuid.UUID:
    user = _user(conn, tenant_id)
    with conn.transaction():
        approval_id = record_approval(
            conn, request_id=request_id, decision=decision, decided_by=user, reason=reason
        )
    return approval_id


def decide_soon(
    dsn: str,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    decision: str,
    *,
    count: int = 1,
    reason: str | None = None,
    seen: list[tuple[str, str | None]] | None = None,
) -> threading.Thread:
    """Answer the run's approval requests as they appear, from another thread, as a person would.

    A person decides while the worker is still holding the tab -- the point of the hold -- so a
    test that called ``run_once`` first and decided afterwards would be testing the
    released-and-restarted behaviour this replaced. The thread waits for each request rather than
    sleeping a fixed time, and uses its own connection: a psycopg connection is not shared between
    threads. ``seen`` records the run's status at each decision, which is how a test asserts what
    the pause looked like from outside.
    """

    def work() -> None:
        answered = 0
        deadline = time.monotonic() + 60.0
        while answered < count and time.monotonic() < deadline:
            with psycopg.connect(dsn, autocommit=True) as conn:
                undecided = [
                    row for row in _requests(conn, run_id) if row["resolved_by_approval_id"] is None
                ]
                if not undecided:
                    time.sleep(0.05)
                    continue
                with conn.cursor() as cur:
                    cur.execute("SELECT status, pause_reason FROM runs WHERE id = %s", (run_id,))
                    status = cur.fetchone()
                if seen is not None:
                    seen.append(status)
                _decide(conn, tenant_id, undecided[0]["id"], decision, reason=reason)
                resume_run(conn, run_id)
            answered += 1

    thread = threading.Thread(target=work, name=f"decide-{decision}", daemon=True)
    thread.start()
    return thread


def _attempts(conn: psycopg.Connection, run_id: uuid.UUID) -> dict[str, int]:
    """``record_key -> attempts`` for a run.

    The database raises ``attempts`` only on ``pending -> claimed``, so one attempt is the proof a
    record was never released and re-queued.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT record_key, attempts FROM run_records WHERE run_id = %s", (run_id,))
        return {row[0]: row[1] for row in cur.fetchall()}


def test_first_record_gate_holds_the_record_at_the_commit_step(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
    database_url: str,
) -> None:
    """The gate holds the tab; approving runs the commit that is already prepared.

    The old behaviour released the record and re-queued it, so the next claim re-ran the recipe from
    its first step. On a real app that restart navigated away from the half-filled form and the
    destination autosaved it (PL-027). `attempts` is the proof it does not happen now: the database
    raises it only on `pending -> claimed`, so one attempt means the record was never let go.
    """
    base = seed(recipe(approval_policy={"commit": "first_record"}))
    for key in ("k1", "k2", "k3"):
        base.add_input_record(key=key, email=f"{key}@x.com")
    run_id = base.create_run()

    seen: list[tuple[str, str | None]] = []
    worker = make_worker(ScriptedRun.build(), gate_wait_seconds=20.0, clock=SystemClock())
    deciding = decide_soon(database_url, base.tenant_id, run_id, "approved", seen=seen)
    assert worker.run_once() is True
    deciding.join(timeout=10)

    # While the person was deciding, the run was paused for the approval and the record was held at
    # its commit step.
    assert seen == [("paused", "awaiting_approval")], seen
    requests = _requests(conn, run_id)
    assert len(requests) == 1, "the first commit is gated even without a preview"
    assert requests[0]["step_id"] == "save"
    assert sum(1 for state in base.states(run_id).values() if state == "filling") == 0

    # Approved: the commit ran where it was prepared, and the rest of the batch followed.
    assert base.states(run_id) == {"k1": "verified", "k2": "verified", "k3": "verified"}
    assert base.run_status(run_id) == ("completed", None)
    assert _attempts(conn, run_id) == {"k1": 1, "k2": 1, "k3": 1}, "a claim is never repeated"


def test_an_undecided_gate_parks_the_record_rather_than_committing_it(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """Nobody decides: the record leaves the tab rather than reaching the destination.

    This is the timeout half of the requirement, and it is why the wait is bounded at all. The run
    stays paused on the request, so a person can still decide it later.
    """
    base = seed(recipe(approval_policy={"commit": "first_record"}))
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build(), gate_wait_seconds=0.5)
    assert worker.run_once() is True

    assert base.states(run_id)["k1"] == "parked"
    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert "nobody decided" in record.reason
    # The run stays paused for a person: the canary is the record that parked, so the pause names
    # that, and the request is still open for a decision that arrives later.
    assert base.run_status(run_id)[0] == "paused"
    assert len(_requests(conn, run_id)) == 1


def test_rejection_parks_with_reviewer_reason(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
    database_url: str,
) -> None:
    base = seed(recipe(approval_policy={"commit": "first_record"}))
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build(), gate_wait_seconds=20.0, clock=SystemClock())
    deciding = decide_soon(
        database_url, base.tenant_id, run_id, "rejected", reason="wrong lead, do not create"
    )
    assert worker.run_once() is True
    deciding.join(timeout=10)

    states = base.states(run_id)
    assert states["k1"] == "parked"
    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.reason == "wrong lead, do not create"


def test_approval_never_covers_unseen_values(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
    database_url: str,
) -> None:
    """An approval answers exactly the values it was shown, so the next record is gated again."""
    base = seed(recipe(approval_policy={"commit": "every_record"}))
    for key in ("k1", "k2"):
        base.add_input_record(key=key, email=f"{key}@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build(), gate_wait_seconds=20.0, clock=SystemClock())
    deciding = decide_soon(database_url, base.tenant_id, run_id, "approved", count=2)
    assert worker.run_once() is True
    deciding.join(timeout=10)

    requests = _requests(conn, run_id)
    assert len(requests) == 2, "another record's values need their own approval"
    assert requests[0]["shown"] != requests[1]["shown"]
    assert base.states(run_id) == {"k1": "verified", "k2": "verified"}


def test_resume_does_not_reopen_an_approved_request(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
    database_url: str,
) -> None:
    base = seed(recipe(approval_policy={"commit": "first_record"}))
    for key in ("k1", "k2"):
        base.add_input_record(key=key, email=f"{key}@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build(), gate_wait_seconds=20.0, clock=SystemClock())
    deciding = decide_soon(database_url, base.tenant_id, run_id, "approved")
    assert worker.run_once() is True
    deciding.join(timeout=10)

    assert len(_requests(conn, run_id)) == 1, "the resumed run must not re-open the request"


def test_approval_row_references_its_request(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe(approval_policy={"commit": "first_record"}))
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True
    request_id = _requests(conn, run_id)[0]["id"]
    approval_id = _decide(conn, base.tenant_id, request_id, "approved")

    with conn.cursor() as cur:
        cur.execute("SELECT request_id, decision FROM approvals WHERE id = %s", (approval_id,))
        assert cur.fetchone() == (request_id, "approved")
    assert _requests(conn, run_id)[0]["resolved_by_approval_id"] == approval_id


def test_an_approval_gate_step_passes_once_approved(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
    database_url: str,
) -> None:
    steps = [
        {"id": "open", "action": "navigate", "url": "http://localhost:5173/x"},
        {"id": "gate", "action": "approval_gate", "reason": "Check {{row.key}} before saving."},
        {
            "id": "save",
            "action": "click",
            "commit": True,
            "assert_after": {"text_visible": "Saved", "timeout_ms": 8000},
            "targets": [{"by": "role", "role": "button", "name": "Save"}],
        },
        {"id": "done", "action": "end_record", "outcome": "verified", "reason": "saved"},
    ]
    base = seed(recipe(steps))
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build(), gate_wait_seconds=20.0, clock=SystemClock())
    deciding = decide_soon(database_url, base.tenant_id, run_id, "approved")
    assert worker.run_once() is True
    deciding.join(timeout=10)

    assert base.states(run_id) == {"k1": "verified"}
    assert len(_requests(conn, run_id)) == 1, "the gate is answered once, and the run carries on"
