"""PL-113 S3: a run row can show the step's own error, for support.

The plain sentence on a parked row says what a person can act on; the step's raw error is what
whoever debugs the run needs, and it was only in ``step_events``, which no screen and no API showed.
``GET /runs/{id}/records`` now carries the latest one as ``last_step_error`` -- the extension's own
words, which never include a value from the row.
"""

from __future__ import annotations

import psycopg
from fastapi.testclient import TestClient

from tests.db.seed import Ledger

RAW = (
    "the click target could not be resolved: the target is not clickable: "
    "the target is outside the window and the page didn't scroll to it"
)


def test_a_records_last_step_error_is_returned(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="parked")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO step_events "
            "(tenant_id, run_record_id, step_id, attempt, started_at, ended_at, outcome, error) "
            "VALUES (%s, %s, 'continue', 1, now(), now(), 'failed', %s)",
            (ledger.tenant_id, record_id, psycopg.types.json.Jsonb(RAW)),
        )

    rows = connected.get(f"/api/v1/runs/{run.id}/records").json()

    assert rows[0]["last_step_error"] == RAW


def test_a_record_with_no_failed_step_has_no_last_step_error(
    connected: TestClient, ledger: Ledger
) -> None:
    run = ledger.new_run()
    ledger.add_record(run, state="pending")

    rows = connected.get(f"/api/v1/runs/{run.id}/records").json()

    assert rows[0]["last_step_error"] is None


def test_the_latest_error_wins(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A row that failed twice shows the last thing that went wrong, not the first."""
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="parked")
    with conn.cursor() as cur:
        for error in ('"the first failure"', '"the second failure"'):
            cur.execute(
                "INSERT INTO step_events "
                "(tenant_id, run_record_id, step_id, attempt, started_at, ended_at, outcome, "
                "error) VALUES (%s, %s, 'continue', 1, now(), now(), 'failed', %s)",
                (ledger.tenant_id, record_id, psycopg.types.json.Jsonb(error.strip('"'))),
            )

    rows = connected.get(f"/api/v1/runs/{run.id}/records").json()

    assert rows[0]["last_step_error"] == "the second failure"
