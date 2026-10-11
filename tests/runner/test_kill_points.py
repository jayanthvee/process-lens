"""The kill test: a crash at each write-ahead point leaves no duplicate and no stuck record.

Four write-ahead points, four repetitions each, a fresh worker taking over after the crash. The dead
worker's lease is made stale by backdating ``heartbeat_at`` in test SQL (S6), never by a test-only
branch in production code.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg
import pytest

from services.runner.testing import (
    CrashHooks,
    FakeDestination,
    FakeExecutor,
    FakeReconciler,
    FrozenClock,
    SimulatedCrash,
)
from services.runner.worker import Worker
from tests.runner.conftest import Seed

WRITE_AHEAD_POINTS = ("after_submitting", "after_dispatch", "after_ack", "after_assert")

EXIT_STATES = {"verified", "skipped", "parked", "failed"}


def _worker(
    database_url: str,
    destination: FakeDestination,
    *,
    worker_id: str,
    crash_at: tuple[str, ...] = (),
) -> Worker:
    return Worker(
        database_url,
        worker_id=worker_id,
        executor=FakeExecutor(destination),
        reconciler=FakeReconciler(destination),
        clock=FrozenClock(),
        crash_hooks=CrashHooks(crash_at) if crash_at else None,
        heartbeat_interval=3600.0,
    )


@pytest.mark.parametrize("point", WRITE_AHEAD_POINTS)
def test_crash_at_each_write_ahead_point_leaves_no_duplicates(
    point: str,
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
    database_url: str,
) -> None:
    for _repetition in range(4):
        base = seed()
        keys = [f"r{index}" for index in range(5)]
        for key in keys:
            base.add_input_record(key=key, email=f"{key}@x.com")
        run_id = base.create_run()

        destination = FakeDestination()
        dying = _worker(database_url, destination, worker_id="w1", crash_at=(point,))
        with pytest.raises(SimulatedCrash):
            dying.run_once()

        # The dead worker's heartbeat is stale; a new worker takes the run over (S6).
        conn.execute(
            "UPDATE runs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", (run_id,)
        )
        recovering = _worker(database_url, destination, worker_id="w2")
        assert recovering.run_once() is True

        assert destination.duplicates == 0, f"a crash at {point} must never double-commit"
        assert destination.all_keys() == set(keys), "every commit landed exactly once"
        states = base.states(run_id)
        assert set(states.values()) <= EXIT_STATES, f"a record was left open: {states}"
        assert all(state == "verified" for state in states.values())


@pytest.mark.parametrize("point", WRITE_AHEAD_POINTS)
def test_a_crashed_run_is_recovered_and_the_ledger_is_consistent(
    point: str,
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
    database_url: str,
) -> None:
    base = seed()
    base.add_input_record(key="only", email="only@x.com")
    run_id = base.create_run()

    destination = FakeDestination()
    dying = _worker(database_url, destination, worker_id="w1", crash_at=(point,))
    with pytest.raises(SimulatedCrash):
        dying.run_once()

    conn.execute(
        "UPDATE runs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", (run_id,)
    )
    recovering = _worker(database_url, destination, worker_id="w2")
    assert recovering.run_once() is True

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM committed_keys k JOIN run_records r ON r.id = k.run_record_id "
            "WHERE r.run_id = %s",
            (run_id,),
        )
        key_rows = int(cur.fetchone()[0])
        cur.execute(
            "SELECT count(*) FROM run_records WHERE run_id = %s AND state = 'verified'", (run_id,)
        )
        verified = int(cur.fetchone()[0])
    assert key_rows == verified == 1, "a verified record implies exactly one committed key"
    assert isinstance(run_id, uuid.UUID)
