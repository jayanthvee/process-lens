"""The commit window: the prepare/dispatch split (R2), the acknowledgement order (S1)."""

from __future__ import annotations

from collections.abc import Callable

import psycopg

from packages.ledger import get_record
from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed


def _transitions(conn: psycopg.Connection, run_id) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.from_state || '>' || t.to_state FROM run_record_transitions t "
            "JOIN run_records r ON r.id = t.run_record_id WHERE r.run_id = %s ORDER BY t.id",
            (run_id,),
        )
        return [row[0] for row in cur.fetchall()]


def test_prepare_failure_parks_without_a_write_ahead(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(plan={"save": "locator_failure"})
    worker = make_worker(scripted)
    assert worker.run_once() is True

    record_id = base.record_ids(run_id)[0]
    record = get_record(conn, record_id)
    assert record is not None
    assert record.state == "parked"
    assert record.commit_attempted is False, "a locator failure is never a commit attempt"
    assert "submitting" not in " ".join(_transitions(conn, run_id))


def test_not_dispatched_fails_from_submitting(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(plan={"save": "lost_dispatch"})
    worker = make_worker(scripted)
    assert worker.run_once() is True

    record_id = base.record_ids(run_id)[0]
    record = get_record(conn, record_id)
    assert record is not None
    assert record.state == "failed", "the worker knew no input event was sent (L3)"
    assert "submitting>failed" in _transitions(conn, run_id)


def test_assertion_timeout_goes_to_reconciling_never_failed(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(conditions={"save": False})
    worker = make_worker(scripted)
    assert worker.run_once() is True

    transitions = _transitions(conn, run_id)
    assert "submitted_unverified>reconciling" in transitions
    assert "failed" not in " ".join(transitions), "a timeout is not proof of failure"
    # The destination has the key, so reconcile finds exactly one and verifies it.
    assert base.states(run_id) == {"row-0001": "verified"}


def test_rejection_without_acknowledgement_does_not_raise_state_conflict(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """S1: the ack is checked before a rejection, so it never applies from `submitting`."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(plan={"save": "rejected_no_ack"})
    scripted.destination.seed("row-0001")  # the destination does have it, so reconcile finds one
    worker = make_worker(scripted)
    assert worker.run_once() is True  # no LedgerError / StateConflict escapes

    transitions = _transitions(conn, run_id)
    assert "submitted_unverified>failed" not in transitions, (
        "a rejection without an acknowledgement is never applied from submitting"
    )
    assert "submitting>reconciling" in transitions, (
        "with no ack the outcome is unknown, so it reconciles"
    )
    assert base.states(run_id) == {"row-0001": "verified"}


def test_a_positive_rejection_after_the_acknowledgement_fails_the_record(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(plan={"save": "rejected"})
    worker = make_worker(scripted)
    assert worker.run_once() is True

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "failed"
    assert "submitted_unverified>failed" in _transitions(conn, run_id)
