"""PL-041 part 3: a task that keeps failing says so.

The owner's old task "New CRM Lead" looked healthy in the task list while every run parked its
rows for the same reason. A saved task never changes, so a task recorded before a fix keeps
failing the same way, and nothing said so. ``GET /api/v1/tasks`` now carries ``keeps_failing``:
set when the task's two most recent finished runs both ended with no added row and at least one
row that needs a person, for the same reason.

The style is ``tests/api/test_runs_reads.py``'s: the ``ledger`` fixture builds a fresh tenant, so
every assertion is scoped to this test's own data in the shared session database.
"""

from __future__ import annotations

import uuid

import psycopg
from fastapi.testclient import TestClient

from tests.db.seed import Ledger

SAME_REASON = "the confirmation was already on the page before saving"


def finished_run(
    conn: psycopg.Connection,
    ledger: Ledger,
    *,
    hours_ago: int,
    parked: int = 0,
    failed: int = 0,
    added: int = 0,
    reason: str | None = None,
) -> uuid.UUID:
    """One finished run of the ledger's task, with the rows a test needs and its own creation time.

    ``packed`` rows and ``failed`` rows carry ``reason``; ``added`` rows reached ``verified``. The
    time is explicit so "the two most recent" is a fact and not a race on ``now()``.
    """
    run = ledger.new_run()
    for _ in range(added):
        ledger.add_record(run, state="verified")
    for state, count in (("parked", parked), ("failed", failed)):
        for _ in range(count):
            record_id = ledger.add_record(run, state=state)
            if reason is not None:
                conn.execute(
                    "UPDATE run_records SET reason = %s WHERE id = %s", (reason, record_id)
                )
    conn.execute(
        "UPDATE runs SET status = 'completed', created_at = now() - make_interval(hours => %s) "
        "WHERE id = %s",
        (hours_ago, run.id),
    )
    return run.id


def task_of(connected: TestClient, ledger: Ledger) -> dict:
    """This test's task, from ``GET /api/v1/tasks``."""
    response = connected.get(f"/api/v1/tasks?tenant_id={ledger.tenant_id}")
    assert response.status_code == 200, response.text
    rows = [row for row in response.json() if row["workflow_id"] == str(ledger.workflow_id)]
    assert len(rows) == 1, response.text
    return rows[0]


def test_two_failing_runs_with_the_same_reason_flag_the_task(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """No added row in either run, a row that needs a person in each, and one reason in common."""
    finished_run(conn, ledger, hours_ago=2, parked=1, reason=SAME_REASON)
    finished_run(conn, ledger, hours_ago=1, failed=1, reason=SAME_REASON)

    assert task_of(connected, ledger)["keeps_failing"] == {"reason": SAME_REASON}


def test_one_good_run_among_the_last_two_clears_it(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The task worked the last time it ran, so it is not a task that keeps failing."""
    finished_run(conn, ledger, hours_ago=2, parked=2, reason=SAME_REASON)
    finished_run(conn, ledger, hours_ago=1, added=1, parked=1, reason=SAME_REASON)

    assert task_of(connected, ledger)["keeps_failing"] is None


def test_different_reasons_do_not_flag_it(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Two different problems are two problems, not one task that stopped working."""
    finished_run(conn, ledger, hours_ago=2, parked=1, reason="a value is missing")
    finished_run(conn, ledger, hours_ago=1, parked=1, reason="the option doesn't exist")

    assert task_of(connected, ledger)["keeps_failing"] is None


def test_a_task_that_has_never_run_is_not_failing(connected: TestClient, ledger: Ledger) -> None:
    """Nothing to compare, so nothing to flag."""
    assert task_of(connected, ledger)["keeps_failing"] is None


def test_only_the_two_most_recent_finished_runs_count(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """An old failing pair followed by a good run stays clear; the newest two are what count."""
    finished_run(conn, ledger, hours_ago=3, parked=1, reason=SAME_REASON)
    finished_run(conn, ledger, hours_ago=2, parked=1, reason=SAME_REASON)
    finished_run(conn, ledger, hours_ago=1, added=2)

    assert task_of(connected, ledger)["keeps_failing"] is None


def test_a_run_that_is_still_going_is_not_one_of_the_last_two(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A queued run has not finished, so it cannot clear or earn the warning yet."""
    finished_run(conn, ledger, hours_ago=2, parked=1, reason=SAME_REASON)
    finished_run(conn, ledger, hours_ago=1, parked=1, reason=SAME_REASON)
    ledger.new_run()

    assert task_of(connected, ledger)["keeps_failing"] == {"reason": SAME_REASON}
