"""Dismissing a parked record: the trigger is the authority, and its refusal is the 409."""

from __future__ import annotations

import uuid

import psycopg
from fastapi.testclient import TestClient

from tests.db.seed import Ledger


def _state_of(conn: psycopg.Connection, record_id: uuid.UUID) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT state FROM run_records WHERE id = %s", (record_id,))
        return cur.fetchone()[0]


def _reviewer(conn: psycopg.Connection, ledger: Ledger) -> uuid.UUID:
    """A user row: ``destination_checked_by`` is a foreign key, so a check names a real person."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (tenant_id, email, name, role) "
            "VALUES (%s, %s, 'Reviewer', 'reviewer') RETURNING id",
            (ledger.tenant_id, f"reviewer-{uuid.uuid4().hex[:6]}@example.com"),
        )
        return cur.fetchone()[0]


def _commit_attempted_parked(
    conn: psycopg.Connection, ledger: Ledger
) -> tuple[uuid.UUID, uuid.UUID]:
    """A run and a parked record that entered the commit window (``commit_attempted``)."""
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="reconciling")
    conn.execute(
        "UPDATE run_records SET state = 'parked', reason = 'ambiguous' WHERE id = %s", (record_id,)
    )
    return run.id, record_id


def test_dismiss_a_non_commit_record_goes_to_skipped(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A record that never reached a commit is dismissed in one click."""
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="parked")

    response = connected.post(f"/api/v1/records/{record_id}/dismiss", json={"reason": "not needed"})

    assert response.status_code == 200, response.text
    assert response.json() == {"record_id": str(record_id), "state": "skipped"}
    assert _state_of(conn, record_id) == "skipped"


def test_dismiss_a_commit_attempted_record_without_a_check_is_409(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A record that may already have saved is not dismissed without a check, and stays parked."""
    _run_id, record_id = _commit_attempted_parked(conn, ledger)

    response = connected.post(f"/api/v1/records/{record_id}/dismiss", json={"reason": "stale"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"]
    assert _state_of(conn, record_id) == "parked"


def test_dismiss_a_commit_attempted_record_with_a_check_is_dismissed(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A person confirmed the destination does not have it, so the record may be skipped."""
    _run_id, record_id = _commit_attempted_parked(conn, ledger)
    reviewer_id = _reviewer(conn, ledger)

    response = connected.post(
        f"/api/v1/records/{record_id}/dismiss",
        json={"reason": "checked the site", "destination_checked_by": str(reviewer_id)},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"record_id": str(record_id), "state": "skipped"}
    assert _state_of(conn, record_id) == "skipped"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT destination_checked_by, destination_checked_at FROM run_records WHERE id = %s",
            (record_id,),
        )
        checked_by, checked_at = cur.fetchone()
    assert checked_by == reviewer_id
    assert checked_at is not None


def test_dismiss_a_verified_record_is_409(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A verified record is not parked, and its outcome is never rewritten as skipped."""
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="verified")

    response = connected.post(f"/api/v1/records/{record_id}/dismiss", json={})

    assert response.status_code == 409, response.text
    assert _state_of(conn, record_id) == "verified"


def test_dismiss_a_prechecked_record_is_409(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A prechecked record is being worked by a runner, so dismiss never skips it."""
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="prechecked")

    response = connected.post(f"/api/v1/records/{record_id}/dismiss", json={})

    assert response.status_code == 409, response.text
    assert _state_of(conn, record_id) == "prechecked"


def test_dismiss_a_missing_record_is_404(connected: TestClient) -> None:
    assert connected.post(f"/api/v1/records/{uuid.uuid4()}/dismiss", json={}).status_code == 404
