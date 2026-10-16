"""PL-030: the read the demo supervisor polls -- which runs could a worker take.

The decision that consumes these rows is a pure function
(``tests/demo/test_demo.py``); this is the database half of that: a queued run
has no live worker, a running run's lease is live only while its heartbeat is
fresh, and anything else is not listed at all.

The read is deliberately not tenant-scoped -- the demo is one local tenant --
so every test here picks its own runs out of the shared session database.
"""

from __future__ import annotations

import psycopg

from packages.ledger import list_open_runs
from tests.db.seed import Ledger


def test_a_queued_run_has_no_live_worker(conn: psycopg.Connection, ledger: Ledger) -> None:
    """Nobody has claimed it, so a worker should be started for it."""
    run = ledger.new_run()

    rows = [row for row in list_open_runs(conn) if row.run_id == run.id]

    assert [row.status for row in rows] == ["queued"]
    assert rows[0].worker_live is False


def test_a_running_run_with_a_fresh_heartbeat_is_live(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Its own worker holds the lease: the supervisor must not race it."""
    run = ledger.new_run()
    conn.execute(
        "UPDATE runs SET status = 'running', worker_id = 'w1', heartbeat_at = now() WHERE id = %s",
        (run.id,),
    )

    rows = [row for row in list_open_runs(conn) if row.run_id == run.id]

    assert [row.status for row in rows] == ["running"]
    assert rows[0].worker_live is True


def test_a_running_run_whose_heartbeat_lapsed_is_not_live(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The lease says nobody is driving: the supervisor steps in."""
    run = ledger.new_run()
    conn.execute(
        "UPDATE runs SET status = 'running', worker_id = 'w1', "
        "heartbeat_at = now() - interval '1 hour' WHERE id = %s",
        (run.id,),
    )

    rows = [row for row in list_open_runs(conn) if row.run_id == run.id]

    assert rows[0].worker_live is False


def test_paused_and_finished_runs_are_not_listed(conn: psycopg.Connection, ledger: Ledger) -> None:
    """A paused run waits for a person; a finished one waits for nobody."""
    done = ledger.new_run()
    paused = ledger.new_run()
    conn.execute(
        "UPDATE runs SET status = 'completed', finished_at = now() WHERE id = %s", (done.id,)
    )
    conn.execute("UPDATE runs SET status = 'paused' WHERE id = %s", (paused.id,))

    listed = {row.run_id for row in list_open_runs(conn)}

    assert done.id not in listed
    assert paused.id not in listed
