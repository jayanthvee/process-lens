"""B1: pause, resume and cancel hand a run between workers without ever double-submitting.

A paused run may only be re-queued for a fresh worker when no live worker holds it. If a live worker
still holds it (its heartbeat is fresh), a resume or a cancel flips it back to ``running`` so that
holder carries on, and a worker that stops hands the run back so it is never left unclaimed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg

from packages.ledger import (
    claim_run,
    get_run,
    release_run,
    request_cancel,
    resume_run,
    set_run_status,
)
from packages.ledger.runs import DEFAULT_HEARTBEAT_TIMEOUT
from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed


def _status(conn: psycopg.Connection, run_id: uuid.UUID) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM runs WHERE id = %s", (run_id,))
        return cur.fetchone()[0]


def _holder(conn: psycopg.Connection, run_id: uuid.UUID) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT worker_id FROM runs WHERE id = %s", (run_id,))
        return cur.fetchone()[0]


def _stale(conn: psycopg.Connection, run_id: uuid.UUID) -> None:
    conn.execute(
        "UPDATE runs SET heartbeat_at = now() - interval '2 hours' WHERE id = %s", (run_id,)
    )


# --- resume -----------------------------------------------------------------


def test_resume_of_a_run_a_live_worker_holds_goes_back_to_running(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    """The holder continues; a fresh worker is not invited to claim the run."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    assert claim_run(conn, "w1") is not None

    set_run_status(conn, run_id, "paused", pause_reason="paused_by_operator")
    assert resume_run(conn, run_id) is True

    assert _status(conn, run_id) == "running", "a live holder carries on; the run is not re-queued"
    assert claim_run(conn, "w2") is None, "no second worker may claim it"


def test_resume_of_a_released_run_queues_it(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    assert claim_run(conn, "w1") is not None
    set_run_status(conn, run_id, "paused", pause_reason="paused_by_operator")
    assert release_run(conn, run_id, "w1") is True

    assert resume_run(conn, run_id) is True

    assert _status(conn, run_id) == "queued"
    assert claim_run(conn, "w2") is not None, "a released run can be claimed again"


def test_resume_of_a_stale_run_queues_it(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    assert claim_run(conn, "w1") is not None
    set_run_status(conn, run_id, "paused", pause_reason="paused_by_operator")
    _stale(conn, run_id)  # the holder died without releasing

    assert resume_run(conn, run_id) is True

    assert _status(conn, run_id) == "queued"
    assert claim_run(conn, "w2") is not None


def test_resume_of_a_running_or_queued_run_is_not_a_resume(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    assert resume_run(conn, run_id) is False, "a queued run is not paused"
    assert claim_run(conn, "w1") is not None
    assert resume_run(conn, run_id) is False, "a running run is not paused"


def test_resume_of_a_missing_run_is_false(conn: psycopg.Connection) -> None:
    assert resume_run(conn, uuid.uuid4()) is False


# --- cancel -----------------------------------------------------------------


def test_cancel_of_a_run_a_live_worker_holds_goes_back_to_running(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    assert claim_run(conn, "w1") is not None
    set_run_status(conn, run_id, "paused", pause_reason="paused_by_operator")

    assert request_cancel(conn, run_id) is True

    assert _status(conn, run_id) == "running", "the holder notices the cancel request and finalises"
    assert _holder(conn, run_id) == "w1"


def test_cancel_of_a_released_run_queues_it_for_a_worker_to_finalise(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    assert claim_run(conn, "w1") is not None
    set_run_status(conn, run_id, "paused", pause_reason="paused_by_operator")
    assert release_run(conn, run_id, "w1") is True

    assert request_cancel(conn, run_id) is True

    assert _status(conn, run_id) == "queued"
    assert claim_run(conn, "w2") is not None


def test_cancel_of_a_running_run_leaves_the_status_alone(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    assert claim_run(conn, "w1") is not None

    assert request_cancel(conn, run_id) is True

    assert _status(conn, run_id) == "running"
    run = get_run(conn, run_id)
    assert run is not None and run.cancel_requested_at is not None


def test_cancel_of_a_finished_run_is_not_asked(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    conn.execute("UPDATE runs SET status = 'completed' WHERE id = %s", (run_id,))

    assert request_cancel(conn, run_id) is False


# --- release ----------------------------------------------------------------


def test_release_only_hands_back_a_paused_run_the_worker_holds(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    assert claim_run(conn, "w1") is not None

    assert release_run(conn, run_id, "w1") is False, "a running run is not released"
    assert _holder(conn, run_id) == "w1"

    set_run_status(conn, run_id, "paused", pause_reason="paused_by_operator")
    assert release_run(conn, run_id, "w2") is False, "only the holder may release it"
    assert _holder(conn, run_id) == "w1"

    assert release_run(conn, run_id, "w1") is True
    assert _holder(conn, run_id) is None


def test_the_default_heartbeat_timeout_outlives_the_longest_legal_step() -> None:
    """A live worker is never mistaken for a dead one: the threshold exceeds 3x the schema's cap."""
    assert DEFAULT_HEARTBEAT_TIMEOUT == "365 seconds"


# --- the holder continues ---------------------------------------------------


def test_a_quick_resume_does_not_interrupt_the_holder(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """The acceptance the review asks for: after a pause and an immediate resume, the holder
    finishes the record itself, with no second worker and no second submission."""
    base = seed()
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    original = scripted.executor.check_condition
    state = {"paused_once": False}

    def check(condition, *, timeout_ms, step_id=None, record_key=None):  # type: ignore[no-untyped-def]
        if not state["paused_once"]:
            state["paused_once"] = True
            # The operator pauses and immediately resumes while this worker is mid-record.
            set_run_status(conn, run_id, "paused", pause_reason="paused_by_operator")
            assert resume_run(conn, run_id) is True
            assert _status(conn, run_id) == "running", "the holder keeps the run"
        return original(condition, timeout_ms=timeout_ms, step_id=step_id, record_key=record_key)

    scripted.executor.check_condition = check  # type: ignore[method-assign]

    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.states(run_id) == {"k1": "verified"}, "the holder finished the record itself"
    assert base.run_status(run_id) == ("completed", None)
    assert scripted.destination.duplicates == 0
    assert _holder(conn, run_id) == "worker-1", "no second worker took the run"


def test_a_worker_that_stops_on_a_pause_hands_the_run_back(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """A pause the worker obeys releases it, so a later resume queues it for a fresh worker."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    original = scripted.executor.perform
    calls = {"n": 0}

    def perform(command, on_dispatched=None):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            set_run_status(conn, run_id, "paused", pause_reason="paused_by_operator")
        return original(command, on_dispatched)

    scripted.executor.perform = perform  # type: ignore[method-assign]

    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert _status(conn, run_id) == "paused"
    assert _holder(conn, run_id) is None, "the stopped worker handed the run back"

    assert resume_run(conn, run_id) is True
    assert _status(conn, run_id) == "queued"
    assert claim_run(conn, "worker-2") is not None
    assert calls["n"] == 1, "the worker stopped at the boundary"
