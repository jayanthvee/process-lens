"""The ledger contract, enforced by the database itself.

These tests cover the guarantees the schema triggers and constraints provide:
the legal state transitions of a record, the attempts counter, the cross-run
duplicate guard, the append-only tables, and the run summary view.

They replace the psql smoke script that used to be the only check of this
contract, so every case in it appears here as an assertion.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.db.seed import Ledger

# The claim query a run worker uses: the oldest pending record of a run that
# still has attempts left, skipping rows another worker is holding.
CLAIM_NEXT_RECORD = """
WITH next AS (
  SELECT id FROM run_records
  WHERE run_id = %s AND state = 'pending' AND attempts < max_attempts
  ORDER BY created_at, id
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE run_records r
SET state = 'claimed', claimed_by = %s, claimed_at = now()
FROM next
WHERE r.id = next.id
RETURNING r.id, r.record_key, r.state, r.attempts
"""

INSERT_COMMITTED_KEY = """
INSERT INTO committed_keys
  (tenant_id, destination_key, idempotency_key, workflow_id, run_record_id)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (tenant_id, destination_key, idempotency_key) DO NOTHING
RETURNING id
"""

LEGAL_TRANSITIONS = [
    ("pending", "claimed"),
    ("pending", "skipped"),
    ("pending", "failed"),
    ("claimed", "prechecked"),
    ("claimed", "pending"),
    ("claimed", "parked"),
    ("claimed", "failed"),
    ("prechecked", "filling"),
    ("prechecked", "skipped"),
    ("prechecked", "parked"),
    ("prechecked", "pending"),
    ("filling", "submitting"),
    ("filling", "verified"),  # read-only workflows have no commit step
    ("filling", "parked"),
    ("filling", "failed"),
    ("filling", "pending"),
    ("submitting", "submitted_unverified"),
    ("submitting", "reconciling"),
    ("submitting", "failed"),
    ("submitted_unverified", "verified"),
    ("submitted_unverified", "failed"),
    ("submitted_unverified", "reconciling"),
    ("reconciling", "verified"),
    ("reconciling", "pending"),
    ("reconciling", "parked"),
    ("reconciling", "failed"),
    ("parked", "pending"),
    ("parked", "reconciling"),
    ("parked", "skipped"),
    ("failed", "pending"),
    ("failed", "reconciling"),
]

ILLEGAL_TRANSITIONS = [
    ("submitting", "pending"),  # a blind retry after a submit was sent
    ("submitted_unverified", "pending"),
    ("verified", "pending"),
    ("verified", "claimed"),
    ("skipped", "pending"),
    ("submitting", "verified"),
    ("pending", "verified"),
    ("claimed", "submitting"),
    ("reconciling", "filling"),
    ("parked", "claimed"),
]


def _set_state(conn: psycopg.Connection, record_id: uuid.UUID, state: str) -> None:
    conn.execute("UPDATE run_records SET state = %s WHERE id = %s", (state, record_id))


def _state_of(conn: psycopg.Connection, record_id: uuid.UUID) -> tuple[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT state, attempts FROM run_records WHERE id = %s", (record_id,))
        return cur.fetchone()


# --- the claim query -------------------------------------------------------


def test_claim_query_takes_one_record_and_counts_the_attempt(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run, record_ids = ledger.seed_records(2)

    with conn.cursor() as cur:
        cur.execute(CLAIM_NEXT_RECORD, (run.id, "worker-1"))
        claimed = cur.fetchall()

    assert len(claimed) == 1, "the claim query takes exactly one record"
    claimed_id, _record_key, state, attempts = claimed[0]
    assert claimed_id in record_ids
    assert (state, attempts) == ("claimed", 1), "the guard increments attempts on every claim"
    remaining = [record_id for record_id in record_ids if record_id != claimed_id]
    assert _state_of(conn, remaining[0]) == ("pending", 0)


def test_a_record_with_no_attempts_left_is_not_claimed(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    ledger.add_record(run, state="pending", attempts=3, max_attempts=3)

    with conn.cursor() as cur:
        cur.execute(CLAIM_NEXT_RECORD, (run.id, "worker-1"))
        assert cur.fetchall() == []


# --- transitions -----------------------------------------------------------


@pytest.mark.parametrize(("old_state", "new_state"), LEGAL_TRANSITIONS)
def test_legal_transitions_are_accepted(
    conn: psycopg.Connection, ledger: Ledger, old_state: str, new_state: str
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state=old_state)

    _set_state(conn, record_id, new_state)

    assert _state_of(conn, record_id)[0] == new_state


@pytest.mark.parametrize(("old_state", "new_state"), ILLEGAL_TRANSITIONS)
def test_illegal_transitions_are_rejected(
    conn: psycopg.Connection, ledger: Ledger, old_state: str, new_state: str
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state=old_state)

    with pytest.raises(psycopg.errors.RaiseException) as caught:
        _set_state(conn, record_id, new_state)

    assert f"illegal ledger transition {old_state} -> {new_state}" in str(caught.value)
    assert _state_of(conn, record_id)[0] == old_state, "the row is left untouched"


def test_a_submitting_record_cannot_be_blindly_retried(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A record whose click may already have been sent is never sent again silently."""
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="submitting")

    with pytest.raises(psycopg.errors.RaiseException, match="submitting -> pending"):
        _set_state(conn, record_id, "pending")

    _set_state(conn, record_id, "reconciling")
    assert _state_of(conn, record_id)[0] == "reconciling"


def test_a_verified_record_cannot_move_again(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="verified")

    with pytest.raises(psycopg.errors.RaiseException, match="illegal ledger transition"):
        _set_state(conn, record_id, "pending")


def test_attempts_counter_allows_three_claims_and_stops_the_fourth(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, max_attempts=3)

    for expected_attempt in (1, 2, 3):
        _set_state(conn, record_id, "claimed")
        assert _state_of(conn, record_id) == ("claimed", expected_attempt)
        _set_state(conn, record_id, "pending")

    with pytest.raises(psycopg.errors.CheckViolation, match="violates check constraint"):
        _set_state(conn, record_id, "claimed")

    assert _state_of(conn, record_id) == ("pending", 3)


def test_a_record_that_used_all_attempts_can_be_closed_out(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    exhausted = ledger.add_record(run, attempts=3, max_attempts=3)
    retryable = ledger.add_record(run, attempts=2, max_attempts=3)

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE run_records SET state = 'failed', reason = 'attempts exhausted' "
            "WHERE run_id = %s AND state = 'pending' AND attempts >= max_attempts",
            (run.id,),
        )
        assert cur.rowcount == 1

    assert _state_of(conn, exhausted) == ("failed", 3)
    assert _state_of(conn, retryable) == ("pending", 2)


# --- duplicate protection --------------------------------------------------


def test_committing_the_same_key_twice_inserts_no_rows(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="verified")
    params = (ledger.tenant_id, "demo-crm", "ravi@example.com", ledger.workflow_id, record_id)

    with conn.cursor() as cur:
        cur.execute(INSERT_COMMITTED_KEY, params)
        assert len(cur.fetchall()) == 1

        cur.execute(INSERT_COMMITTED_KEY, params)
        assert cur.fetchall() == []
        assert cur.rowcount == 0, "the duplicate insert writes nothing"

    assert ledger.count_committed_keys() == 1


# --- append-only tables ----------------------------------------------------


def test_recipe_versions_cannot_be_changed(conn: psycopg.Connection, ledger: Ledger) -> None:
    with pytest.raises(psycopg.errors.RaiseException, match="recipe_versions is append-only"):
        conn.execute(
            "UPDATE recipe_versions SET recipe = '{}' WHERE id = %s",
            (ledger.recipe_version_id,),
        )

    with pytest.raises(psycopg.errors.RaiseException, match="recipe_versions is append-only"):
        conn.execute("DELETE FROM recipe_versions WHERE id = %s", (ledger.recipe_version_id,))


def test_committed_keys_cannot_be_deleted(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="verified")
    conn.execute(
        INSERT_COMMITTED_KEY,
        (ledger.tenant_id, "demo-crm", "ana@example.com", ledger.workflow_id, record_id),
    )

    with pytest.raises(psycopg.errors.RaiseException, match="committed_keys is append-only"):
        conn.execute("DELETE FROM committed_keys WHERE tenant_id = %s", (ledger.tenant_id,))


def test_step_events_cannot_be_changed(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="verified")
    event_id = conn.execute(
        "INSERT INTO step_events "
        "(tenant_id, run_record_id, step_id, attempt, started_at, outcome) "
        "VALUES (%s, %s, 'fill-email', 1, now(), 'ok') RETURNING id",
        (ledger.tenant_id, record_id),
    ).fetchone()[0]

    with pytest.raises(psycopg.errors.RaiseException, match="step_events is append-only"):
        conn.execute("UPDATE step_events SET outcome = 'failed' WHERE id = %s", (event_id,))


# --- the results view ------------------------------------------------------


def test_run_summary_counts_every_record(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    for state in ("verified", "skipped", "parked", "failed", "pending", "submitted_unverified"):
        ledger.add_record(run, state=state)
    other_run, _ = ledger.seed_records(1, state="parked")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT total, verified, skipped, parked, failed, in_progress "
            "FROM run_summary WHERE run_id = %s",
            (run.id,),
        )
        assert cur.fetchone() == (6, 1, 1, 1, 1, 2)

        cur.execute("SELECT count(*) FROM run_summary WHERE run_id = %s", (other_run.id,))
        assert cur.fetchone()[0] == 1, "each run has its own summary row"
