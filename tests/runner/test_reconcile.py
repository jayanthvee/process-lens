"""Reconcile before any retry: one match verifies, none retries, ambiguity or error parks (S2)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg

from packages.ledger import get_record
from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed


def _transitions(conn: psycopg.Connection, run_id: uuid.UUID) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.from_state || '>' || t.to_state FROM run_record_transitions t "
            "JOIN run_records r ON r.id = t.run_record_id WHERE r.run_id = %s ORDER BY t.id",
            (run_id,),
        )
        return [row[0] for row in cur.fetchall()]


def _assertion_times_out(scripted: ScriptedRun) -> None:
    """Make the commit's assertion fail, so the record lands in reconciling."""
    scripted.executor.conditions["save"] = False


def test_reconcile_one_match_verifies(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()  # the fake commits, so the destination has the key
    _assertion_times_out(scripted)
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.states(run_id) == {"row-0001": "verified"}
    assert "reconciling>verified" in _transitions(conn, run_id)


def test_reconcile_no_match_returns_to_pending(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(plan={"save": "no_commit"})  # acknowledged, but nothing was saved
    _assertion_times_out(scripted)
    worker = make_worker(scripted)
    assert worker.run_once() is True

    transitions = _transitions(conn, run_id)
    assert "reconciling>pending" in transitions, "the destination does not have it, so retry"
    assert "reconciling>verified" not in transitions


def test_reconcile_ambiguous_parks(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(ambiguous=["row-0001"])
    _assertion_times_out(scripted)
    worker = make_worker(scripted)
    assert worker.run_once() is True

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "parked"
    assert "reconciling>parked" in _transitions(conn, run_id)


def test_reconcile_error_parks(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """S2: a reconcile error parks the record; it never fails it."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(errors=["row-0001"])
    _assertion_times_out(scripted)
    worker = make_worker(scripted)
    assert worker.run_once() is True

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "parked"
    assert "failed" not in " ".join(_transitions(conn, run_id))


def test_exhausted_records_are_closed_out_before_completion(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """R5: a record that reconcile keeps returning to pending is closed out, not left pending."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(plan={"save": "no_commit"})
    _assertion_times_out(scripted)
    worker = make_worker(scripted)
    assert worker.run_once() is True

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "failed"
    assert record.attempts == record.max_attempts
    assert base.run_status(run_id) == ("completed", None)


def _walk(conn: psycopg.Connection, record_id: uuid.UUID, states: list[str]) -> None:
    for state in states:
        conn.execute("UPDATE run_records SET state = %s WHERE id = %s", (state, record_id))


def test_all_records_in_exit_state_is_false_while_a_record_is_open(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    """R5's guard: a run is completed only when every record is in an exit state."""
    from packages.ledger import all_records_in_exit_state, count_not_exit

    base = seed()
    base.add_input_record(key="a", email="a@x.com")
    base.add_input_record(key="b", email="b@x.com")
    run_id = base.create_run()

    assert all_records_in_exit_state(conn, run_id) is False
    assert count_not_exit(conn, run_id) == 2

    _walk(conn, _record_id(conn, run_id, "a"), ["claimed", "prechecked", "filling", "submitting"])
    _walk(conn, _record_id(conn, run_id, "b"), ["claimed", "prechecked", "filling", "verified"])

    assert all_records_in_exit_state(conn, run_id) is False, "a submitting record is not out yet"

    conn.execute(
        "UPDATE run_records SET state = 'reconciling' WHERE id = %s",
        (_record_id(conn, run_id, "a"),),
    )
    conn.execute(
        "UPDATE run_records SET state = 'parked' WHERE id = %s", (_record_id(conn, run_id, "a"),)
    )
    assert all_records_in_exit_state(conn, run_id) is True


def test_recovery_reconciles_the_commit_window_before_claiming(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """A caller recovers the window a stopped worker left, then finishes the batch."""
    base = seed()
    base.add_input_record(key="a", email="a@x.com")
    base.add_input_record(key="b", email="b@x.com")
    run_id = base.create_run()

    stuck = _record_id(conn, run_id, "a")
    _walk(conn, stuck, ["claimed", "prechecked", "filling", "submitting"])
    scripted = ScriptedRun.build()
    scripted.destination.seed("a")

    worker = make_worker(scripted)
    assert worker.run_once() is True

    states = base.states(run_id)
    assert states == {"a": "verified", "b": "verified"}, "the strand was reconciled, not abandoned"
    assert base.run_status(run_id) == ("completed", None)


def _record_id(conn: psycopg.Connection, run_id, record_key: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM run_records WHERE run_id = %s AND record_key = %s", (run_id, record_key)
        )
        return cur.fetchone()[0]
