"""Migration 0003: the new transition, the fresh destination check, and approval requests.

These run against the real schema: a ledger row is born ``pending`` and walks to its state along
transitions, so a test cannot forge a state the database would reject in production.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.db.seed import Ledger


def _user(conn: psycopg.Connection, tenant_id: uuid.UUID) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (tenant_id, email, name, role) "
            "VALUES (%s, %s, 'A Person', 'reviewer') RETURNING id",
            (tenant_id, f"person-{uuid.uuid4().hex[:8]}@example.com"),
        )
        return cur.fetchone()[0]


def _advance(conn: psycopg.Connection, record_id: uuid.UUID, states: list[str]) -> None:
    for state in states:
        conn.execute(
            "UPDATE run_records SET state = %s WHERE id = %s AND state <> %s",
            (state, record_id, state),
        )


def _state(conn: psycopg.Connection, record_id: uuid.UUID) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT state FROM run_records WHERE id = %s", (record_id,))
        return cur.fetchone()[0]


def _commit_attempted_filling(conn: psycopg.Connection, ledger: Ledger) -> uuid.UUID:
    """A record back in ``filling`` that has already entered the commit window."""
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="filling")
    _advance(
        conn,
        record_id,
        ["submitting", "reconciling", "pending", "claimed", "prechecked", "filling"],
    )
    return record_id


# --- R1: filling -> skipped -------------------------------------------------


def test_filling_to_skipped_is_accepted_when_never_committed(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="filling")

    conn.execute("UPDATE run_records SET state = 'skipped' WHERE id = %s", (record_id,))

    assert _state(conn, record_id) == "skipped"


def test_filling_to_skipped_is_refused_once_commit_attempted(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    record_id = _commit_attempted_filling(conn, ledger)

    with pytest.raises(psycopg.errors.RaiseException, match="cannot end skipped from filling"):
        conn.execute("UPDATE run_records SET state = 'skipped' WHERE id = %s", (record_id,))

    assert _state(conn, record_id) == "filling"


# --- the fresh destination check (PL-012 follow-up 1) -----------------------


def _commit_attempted_failed(conn: psycopg.Connection, ledger: Ledger) -> uuid.UUID:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="submitting")
    conn.execute("UPDATE run_records SET state = 'failed' WHERE id = %s", (record_id,))
    return record_id


def test_a_fresh_destination_check_is_accepted(conn: psycopg.Connection, ledger: Ledger) -> None:
    record_id = _commit_attempted_failed(conn, ledger)
    user = _user(conn, ledger.tenant_id)

    conn.execute(
        "UPDATE run_records SET state = 'pending', destination_checked_by = %s, "
        "destination_checked_at = now() WHERE id = %s",
        (user, record_id),
    )

    assert _state(conn, record_id) == "pending"


def test_a_backdated_destination_check_is_rejected(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    record_id = _commit_attempted_failed(conn, ledger)
    user = _user(conn, ledger.tenant_id)

    with pytest.raises(psycopg.errors.RaiseException, match="destination_checked_"):
        conn.execute(
            "UPDATE run_records SET state = 'pending', destination_checked_by = %s, "
            "destination_checked_at = now() - interval '10 minutes' WHERE id = %s",
            (user, record_id),
        )

    assert _state(conn, record_id) == "failed"


def test_a_future_destination_check_is_rejected(conn: psycopg.Connection, ledger: Ledger) -> None:
    record_id = _commit_attempted_failed(conn, ledger)
    user = _user(conn, ledger.tenant_id)

    with pytest.raises(psycopg.errors.RaiseException, match="destination_checked_"):
        conn.execute(
            "UPDATE run_records SET state = 'pending', destination_checked_by = %s, "
            "destination_checked_at = now() + interval '1 minute' WHERE id = %s",
            (user, record_id),
        )

    assert _state(conn, record_id) == "failed"


# --- approval requests ------------------------------------------------------


def _request(
    conn: psycopg.Connection, ledger: Ledger, run_id: uuid.UUID, record_id: uuid.UUID
) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO approval_requests "
            "(tenant_id, run_id, run_record_id, step_id, kind, shown) "
            "VALUES (%s, %s, %s, 'save-lead', 'commit', %s) RETURNING id",
            (ledger.tenant_id, run_id, record_id, '{"email": "a@x.com"}'),
        )
        return cur.fetchone()[0]


def test_an_approval_request_resolves_once(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run)
    user = _user(conn, ledger.tenant_id)
    request_id = _request(conn, ledger, run.id, record_id)

    approval_id = conn.execute(
        "INSERT INTO approvals (tenant_id, run_id, run_record_id, step_id, kind, shown, "
        " decision, decided_by, request_id, reason) "
        "VALUES (%s, %s, %s, 'save-lead', 'commit', '{}', 'approved', %s, %s, 'looks right') "
        "RETURNING id",
        (ledger.tenant_id, run.id, record_id, user, request_id),
    ).fetchone()[0]

    conn.execute(
        "UPDATE approval_requests SET resolved_by_approval_id = %s WHERE id = %s",
        (approval_id, request_id),
    )

    with pytest.raises(psycopg.errors.RaiseException, match="already resolved"):
        conn.execute(
            "UPDATE approval_requests SET resolved_by_approval_id = %s WHERE id = %s",
            (approval_id, request_id),
        )


def test_an_approval_request_rejects_any_other_change(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run)
    request_id = _request(conn, ledger, run.id, record_id)

    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        conn.execute(
            'UPDATE approval_requests SET shown = \'{"email": "evil@x.com"}\' WHERE id = %s',
            (request_id,),
        )
    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        conn.execute(
            "UPDATE approval_requests SET step_id = 'another-step' WHERE id = %s", (request_id,)
        )


def test_an_approval_request_rejects_delete_and_truncate(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run)
    request_id = _request(conn, ledger, run.id, record_id)

    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM approval_requests WHERE id = %s", (request_id,))
    # Postgres also refuses a bare TRUNCATE while `approvals` references the table; CASCADE reaches
    # the statement trigger, which is what must stop it.
    with pytest.raises(psycopg.errors.RaiseException, match="TRUNCATE"):
        conn.execute("TRUNCATE approval_requests CASCADE")

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM approval_requests WHERE id = %s", (request_id,))
        assert cur.fetchone()[0] == 1, "the row survived"


def test_an_approval_can_point_at_its_request(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run)
    user = _user(conn, ledger.tenant_id)
    request_id = _request(conn, ledger, run.id, record_id)

    row = conn.execute(
        "INSERT INTO approvals (tenant_id, run_id, run_record_id, step_id, kind, decision, "
        " decided_by, request_id) VALUES (%s, %s, %s, 'save-lead', 'commit', 'rejected', %s, %s) "
        "RETURNING request_id, kind",
        (ledger.tenant_id, run.id, record_id, user, request_id),
    ).fetchone()

    assert row == (request_id, "commit")


# --- review item 2: parked -> skipped ---------------------------------------


def _commit_attempted_parked(conn: psycopg.Connection, ledger: Ledger) -> uuid.UUID:
    """A record that has entered the commit window and is now parked: commit_attempted is true."""
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="submitting")
    _advance(conn, record_id, ["reconciling", "parked"])
    return record_id


def _commit_attempted_prechecked(conn: psycopg.Connection, ledger: Ledger) -> uuid.UUID:
    """A commit-attempted record back at ``prechecked`` (a reconcile found nothing, so it
    retried)."""
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="submitting")
    _advance(
        conn,
        record_id,
        ["reconciling", "pending", "claimed", "prechecked"],
    )
    return record_id


def test_prechecked_to_skipped_is_refused_once_commit_attempted(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The same rule as `parked -> skipped`: never skip a record that may already have saved.

    Without this, a retry whose reconcile lagged (commit_attempted, back at `prechecked`) could be
    dismissed as `skipped` without anyone confirming the destination does not have it.
    """
    record_id = _commit_attempted_prechecked(conn, ledger)

    with pytest.raises(psycopg.errors.RaiseException, match="requires a fresh destination check"):
        conn.execute("UPDATE run_records SET state = 'skipped' WHERE id = %s", (record_id,))

    assert _state(conn, record_id) == "prechecked"


def test_prechecked_to_skipped_is_accepted_with_a_fresh_destination_check(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    record_id = _commit_attempted_prechecked(conn, ledger)
    user = _user(conn, ledger.tenant_id)

    conn.execute(
        "UPDATE run_records SET state = 'skipped', destination_checked_by = %s, "
        "destination_checked_at = now() WHERE id = %s",
        (user, record_id),
    )

    assert _state(conn, record_id) == "skipped"


def test_prechecked_to_skipped_is_accepted_when_never_committed(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="prechecked")

    conn.execute("UPDATE run_records SET state = 'skipped' WHERE id = %s", (record_id,))

    assert _state(conn, record_id) == "skipped"


def test_parked_to_skipped_is_accepted_when_never_committed(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="parked")

    conn.execute("UPDATE run_records SET state = 'skipped' WHERE id = %s", (record_id,))

    assert _state(conn, record_id) == "skipped"


def test_parked_to_skipped_is_refused_once_commit_attempted(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    record_id = _commit_attempted_parked(conn, ledger)

    with pytest.raises(psycopg.errors.RaiseException, match="requires a fresh destination check"):
        conn.execute("UPDATE run_records SET state = 'skipped' WHERE id = %s", (record_id,))

    assert _state(conn, record_id) == "parked"


def test_parked_to_skipped_is_accepted_with_a_fresh_destination_check(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    record_id = _commit_attempted_parked(conn, ledger)
    user = _user(conn, ledger.tenant_id)

    conn.execute(
        "UPDATE run_records SET state = 'skipped', reason = 'confirmed not in the destination', "
        "destination_checked_by = %s, destination_checked_at = now() WHERE id = %s",
        (user, record_id),
    )

    assert _state(conn, record_id) == "skipped"


def test_parked_to_skipped_refuses_a_backdated_destination_check(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    record_id = _commit_attempted_parked(conn, ledger)
    user = _user(conn, ledger.tenant_id)

    with pytest.raises(psycopg.errors.RaiseException, match="destination_checked_"):
        conn.execute(
            "UPDATE run_records SET state = 'skipped', destination_checked_by = %s, "
            "destination_checked_at = now() - interval '10 minutes' WHERE id = %s",
            (user, record_id),
        )

    assert _state(conn, record_id) == "parked"


def test_parked_to_skipped_refuses_a_future_destination_check(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    record_id = _commit_attempted_parked(conn, ledger)
    user = _user(conn, ledger.tenant_id)

    with pytest.raises(psycopg.errors.RaiseException, match="destination_checked_"):
        conn.execute(
            "UPDATE run_records SET state = 'skipped', destination_checked_by = %s, "
            "destination_checked_at = now() + interval '1 minute' WHERE id = %s",
            (user, record_id),
        )

    assert _state(conn, record_id) == "parked"


# --- the runs column R4 adds ------------------------------------------------


def test_runs_carry_a_cancel_request(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()

    conn.execute("UPDATE runs SET cancel_requested_at = now() WHERE id = %s", (run.id,))

    with conn.cursor() as cur:
        cur.execute("SELECT cancel_requested_at IS NOT NULL FROM runs WHERE id = %s", (run.id,))
        assert cur.fetchone()[0] is True
