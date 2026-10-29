"""PL-130: a run whose worker stopped is taken over at once, not after 365 seconds.

The owner's price run of 2026-10-29 (c31639ab) sat six minutes in "saving it now" after its worker
was replaced. Cancel did nothing, and the next run waited behind it. The run still carried the
stopped worker's last heartbeat, and another worker may take a run over only once that heartbeat is
365 seconds old. The supervisor knows when a worker's process ends, so it marks that worker's runs
lapsed and the next worker takes them over on its next poll.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg

from packages.ledger import release_dead_worker_runs
from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed


def _held_by(conn: psycopg.Connection, run_id, worker_id: str) -> None:
    """The run as a worker leaves it when its process is killed: running, freshly heartbeated."""
    conn.execute(
        "UPDATE runs SET status = 'running', worker_id = %s, lease_epoch = lease_epoch + 1, "
        "heartbeat_at = now(), started_at = now() WHERE id = %s",
        (worker_id, run_id),
    )


def test_a_run_held_by_a_live_worker_is_not_taken(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    _held_by(conn, run_id, "deploy-worker-1")

    assert make_worker(ScriptedRun.build(), worker_id="deploy-worker-2").run_once() is False
    assert base.states(run_id) == {"row-0001": "pending"}


def test_a_stopped_workers_run_is_taken_over_at_once(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    _held_by(conn, run_id, "deploy-worker-1")

    assert release_dead_worker_runs(conn, "deploy-worker-1") == [run_id]
    assert make_worker(ScriptedRun.build(), worker_id="deploy-worker-2").run_once() is True

    assert base.states(run_id) == {"row-0001": "verified"}
    assert base.run_status(run_id) == ("completed", None)


def test_only_the_stopped_workers_runs_are_released(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    mine, other = seed(), seed()
    mine.add_input_record(email="a@x.com")
    other.add_input_record(email="b@x.com")
    mine_run, other_run = mine.create_run(), other.create_run()
    _held_by(conn, mine_run, "deploy-worker-7")
    _held_by(conn, other_run, "deploy-worker-8")

    assert release_dead_worker_runs(conn, "deploy-worker-7") == [mine_run]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT heartbeat_at > now() - interval '1 minute' FROM runs WHERE id = %s",
            (other_run,),
        )
        assert cur.fetchone()[0] is True
