"""The read endpoints the page needs: the runs list, and a run's open approvals."""

from __future__ import annotations

import uuid

import psycopg
from fastapi.testclient import TestClient

from tests.db.seed import Ledger


def test_the_runs_list_is_newest_first_with_recipe_and_counts(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Most recent first, each row carrying its recipe name, version and per-state counts."""
    oldest, _ = ledger.seed_records(3, state="pending")
    middle, _ = ledger.seed_records(1, state="parked")
    newest, _ = ledger.seed_records(1, state="verified")
    # Distinct, future creation times, so this test's three rows are the newest three whatever
    # else the session's database holds, and "newest first" is not a race on now().
    conn.execute(
        "UPDATE runs SET created_at = now() + interval '1 hour' WHERE id = %s", (oldest.id,)
    )
    conn.execute(
        "UPDATE runs SET created_at = now() + interval '2 hours' WHERE id = %s", (middle.id,)
    )
    conn.execute(
        "UPDATE runs SET created_at = now() + interval '3 hours' WHERE id = %s", (newest.id,)
    )

    response = connected.get("/api/v1/runs")

    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["id"] for row in rows[:3]] == [str(newest.id), str(middle.id), str(oldest.id)]
    first = rows[0]
    assert first["status"] == "queued"
    assert first["pause_reason"] is None
    assert first["recipe_name"], "each row carries its recipe's name"
    assert first["recipe_version"] == 1
    # The per-state counts are the states the run's records are actually in: a state with no
    # records is absent, not zero (the ledger aggregates per state; a run with none gives {}).
    assert first["counts"] == {"verified": 1}
    assert rows[1]["counts"] == {"parked": 1}
    assert rows[2]["counts"] == {"pending": 3}


def test_the_runs_list_is_capped_at_fifty(connected: TestClient, ledger: Ledger) -> None:
    """The list is a page, not an export: never more than fifty rows."""
    for _ in range(55):
        ledger.new_run()

    rows = connected.get("/api/v1/runs").json()

    assert len(rows) == 50


def test_the_approvals_endpoint_returns_only_undecided_requests(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A request with a decision is not open, so the page is never asked to decide it twice."""
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="parked")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (tenant_id, email, name, role) "
            "VALUES (%s, %s, 'Reviewer', 'reviewer') RETURNING id",
            (ledger.tenant_id, f"reviewer-{uuid.uuid4().hex[:6]}@example.com"),
        )
        reviewer_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO approval_requests (tenant_id, run_id, run_record_id, step_id, kind, shown)"
            " VALUES (%s, %s, %s, 'save', 'commit', %s) RETURNING id",
            (ledger.tenant_id, run.id, record_id, '{"email": "a@example.com"}'),
        )
        open_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO approval_requests (tenant_id, run_id, run_record_id, step_id, kind) "
            "VALUES (%s, %s, %s, 'save', 'commit') RETURNING id",
            (ledger.tenant_id, run.id, record_id),
        )
        decided_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO approvals (tenant_id, run_id, run_record_id, step_id, kind, decision, "
            "decided_by, request_id) VALUES (%s, %s, %s, 'save', 'commit', 'approved', %s, %s) "
            "RETURNING id",
            (ledger.tenant_id, run.id, record_id, reviewer_id, decided_id),
        )
        approval_id = cur.fetchone()[0]
    conn.execute(
        "UPDATE approval_requests SET resolved_by_approval_id = %s WHERE id = %s",
        (approval_id, decided_id),
    )

    response = connected.get(f"/api/v1/runs/{run.id}/approvals")

    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["id"] for row in rows] == [str(open_id)]
    assert rows[0]["step_id"] == "save"
    assert rows[0]["kind"] == "commit"
    assert rows[0]["shown"] == {"email": "a@example.com"}
    assert rows[0]["run_record_id"] == str(record_id)


def test_the_approvals_of_a_missing_run_are_404(connected: TestClient) -> None:
    assert connected.get(f"/api/v1/runs/{uuid.uuid4()}/approvals").status_code == 404
