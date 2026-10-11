"""Approvals: the policy gate stops a run, and a resume never re-asks for answered values (R3)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg

from packages.ledger import get_record, record_approval, resume_run
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


def test_first_record_gate_stops_without_preview(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe(approval_policy={"commit": "first_record"}))
    for key in ("k1", "k2", "k3"):
        base.add_input_record(key=key, email=f"{key}@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True

    assert base.run_status(run_id) == ("paused", "awaiting_approval")
    requests = _requests(conn, run_id)
    assert len(requests) == 1, "the first commit is gated even without a preview"
    assert requests[0]["step_id"] == "save"
    states = base.states(run_id)
    gated = [key for key, state in states.items() if state != "pending"]
    assert len(gated) == 1, "exactly one record reached the gate, before any other is claimed"
    assert states[gated[0]] == "filling"


def test_approval_resumes_the_run(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe(approval_policy={"commit": "first_record"}))
    for key in ("k1", "k2", "k3"):
        base.add_input_record(key=key, email=f"{key}@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True
    request_id = _requests(conn, run_id)[0]["id"]
    _decide(conn, base.tenant_id, request_id, "approved")
    resume_run(conn, run_id)

    assert worker.run_once() is True

    assert base.states(run_id) == {"k1": "verified", "k2": "verified", "k3": "verified"}
    assert base.run_status(run_id) == ("completed", None)


def test_resume_does_not_reopen_an_approved_request(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe(approval_policy={"commit": "first_record"}))
    for key in ("k1", "k2"):
        base.add_input_record(key=key, email=f"{key}@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True
    request_id = _requests(conn, run_id)[0]["id"]
    _decide(conn, base.tenant_id, request_id, "approved")
    resume_run(conn, run_id)
    assert worker.run_once() is True

    assert len(_requests(conn, run_id)) == 1, "the resumed run must not re-open the request"


def test_rejection_parks_with_reviewer_reason(
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
    _decide(conn, base.tenant_id, request_id, "rejected", reason="wrong lead, do not create")
    resume_run(conn, run_id)
    assert worker.run_once() is True

    states = base.states(run_id)
    assert states["k1"] == "parked"
    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.reason == "wrong lead, do not create"


def test_approval_never_covers_unseen_values(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """An approval answers exactly the values it was shown, so the next record is gated again."""
    base = seed(recipe(approval_policy={"commit": "every_record"}))
    for key in ("k1", "k2"):
        base.add_input_record(key=key, email=f"{key}@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True
    first = _requests(conn, run_id)[0]
    _decide(conn, base.tenant_id, first["id"], "approved")
    resume_run(conn, run_id)

    assert worker.run_once() is True
    requests = _requests(conn, run_id)
    assert len(requests) == 2, "another record's values need their own approval"
    assert requests[0]["shown"] != requests[1]["shown"]

    _decide(conn, base.tenant_id, requests[1]["id"], "approved")
    resume_run(conn, run_id)
    assert worker.run_once() is True

    assert base.states(run_id) == {"k1": "verified", "k2": "verified"}


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

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True
    requests = _requests(conn, run_id)
    assert len(requests) == 1
    assert requests[0]["step_id"] == "gate"

    _decide(conn, base.tenant_id, requests[0]["id"], "approved")
    resume_run(conn, run_id)
    assert worker.run_once() is True

    assert base.states(run_id) == {"k1": "verified"}
    assert len(_requests(conn, run_id)) == 1
