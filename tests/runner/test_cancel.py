"""Cancel: the worker reconciles the commit window, releases the rest, and stops (R4)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

import psycopg

from packages.ledger import claim_run
from services.runner.testing import ScriptedRun
from services.runner.worker import Heartbeat, Worker, default_connect
from tests.runner.conftest import Seed


def _walk(conn: psycopg.Connection, record_id: uuid.UUID, states: list[str]) -> None:
    for state in states:
        conn.execute("UPDATE run_records SET state = %s WHERE id = %s", (state, record_id))


def _record_id(conn: psycopg.Connection, run_id: uuid.UUID, record_key: str) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM run_records WHERE run_id = %s AND record_key = %s", (run_id, record_key)
        )
        return cur.fetchone()[0]


def _transitions(conn: psycopg.Connection, run_id: uuid.UUID) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.from_state || '>' || t.to_state FROM run_record_transitions t "
            "JOIN run_records r ON r.id = t.run_record_id WHERE r.run_id = %s ORDER BY t.id",
            (run_id,),
        )
        return [row[0] for row in cur.fetchall()]


def test_cancel_skips_pending_and_marks_the_run_cancelled(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    for key in ("a", "b", "c"):
        base.add_input_record(key=key, email=f"{key}@x.com")
    run_id = base.create_run()
    conn.execute("UPDATE runs SET cancel_requested_at = now() WHERE id = %s", (run_id,))

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True

    assert base.run_status(run_id) == ("cancelled", None)
    assert set(base.states(run_id).values()) == {"skipped"}


def test_cancel_during_commit_window_reconciles_before_stopping(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    base.add_input_record(key="a", email="a@x.com")
    base.add_input_record(key="b", email="b@x.com")
    run_id = base.create_run()

    # Record `a` is mid-commit when the cancel arrives; the destination does have it.
    stuck = _record_id(conn, run_id, "a")
    _walk(conn, stuck, ["claimed", "prechecked", "filling", "submitting"])
    scripted = ScriptedRun.build()
    scripted.destination.seed("a")
    conn.execute("UPDATE runs SET cancel_requested_at = now() WHERE id = %s", (run_id,))

    worker = make_worker(scripted)
    assert worker.run_once() is True

    states = base.states(run_id)
    assert states["a"] == "verified", "the commit window was reconciled before the run stopped"
    assert states["b"] == "skipped"
    assert base.run_status(run_id) == ("cancelled", None)
    transitions = _transitions(conn, run_id)
    assert "reconciling>verified" in transitions
    assert "pending>skipped" in transitions


def test_a_cancel_that_arrives_as_the_last_record_finishes_is_not_lost(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """R4: the run re-reads the cancel before it completes, so a late cancel is honoured."""
    base = seed()
    base.add_input_record(key="a", email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    original = scripted.executor.check_condition

    def check(condition, *, timeout_ms, step_id=None, record_key=None):  # type: ignore[no-untyped-def]
        # The cancel arrives while the last record's assertion is being checked, after its commit.
        conn.execute("UPDATE runs SET cancel_requested_at = now() WHERE id = %s", (run_id,))
        return original(condition, timeout_ms=timeout_ms, step_id=step_id, record_key=record_key)

    scripted.executor.check_condition = check  # type: ignore[method-assign]

    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.run_status(run_id)[0] == "cancelled", "the cancel was not lost to the completion"


def test_the_heartbeat_runs_while_a_step_blocks(
    seed: Callable[..., Seed], conn: psycopg.Connection, database_url: str
) -> None:
    """R6: the lease is refreshed from its own thread, so a blocking step cannot let it lapse."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    run = claim_run(conn, "w1")
    assert run is not None
    conn.execute("UPDATE runs SET heartbeat_at = now() - interval '1 day' WHERE id = %s", (run_id,))

    beat = Heartbeat(default_connect, database_url, run_id, "w1", run.lease_epoch, interval=0.05)
    beat.start()
    deadline = time.monotonic() + 5
    while beat.beats < 1 and time.monotonic() < deadline:
        time.sleep(0.02)
    beat.stop()

    assert beat.beats >= 1, "the heartbeat thread must beat without the loop's cooperation"
    assert beat.stale_error is None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT heartbeat_at > now() - interval '1 hour' FROM runs WHERE id = %s", (run_id,)
        )
        assert cur.fetchone()[0] is True, "the lease was actually refreshed in the database"
