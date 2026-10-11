"""Ledger hardening (migration 0002): one test per confirmed gap, G1 to G10.

Each test asserts a behaviour the database now enforces. Against 0001 alone the bad action succeeds,
so the test fails; with 0002 it raises, so the test passes. ``.data/pl012_0001_check.py`` runs the
same actions against a 0001-only database and shows the difference.

The transition-matrix test reads its expected list from ``internal/docs/LEDGER_SPEC.md``, so the
spec table and the guard cannot drift apart silently.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import psycopg
import pytest

from tests.db.seed import DESTINATION_KEY, Ledger

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_SPEC = REPO_ROOT / "internal" / "docs" / "LEDGER_SPEC.md"

APPEND_ONLY_TABLES = [
    "recipe_versions",
    "committed_keys",
    "step_events",
    "ai_calls",
    "run_record_transitions",
    "approvals",
    "run_records",
]


def _spec_section(start: str, end: str) -> str:
    text = LEDGER_SPEC.read_text(encoding="utf-8")
    return text.split(start, 1)[1].split(end, 1)[0]


def _doc_states() -> list[str]:
    section = _spec_section("## States", "## Transitions")
    return sorted(set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", section, re.MULTILINE)))


def _doc_transitions() -> set[tuple[str, str]]:
    section = _spec_section("## Transitions", "## Write-ahead")
    return set(re.findall(r"\|\s*`([a-z_]+)`\s*\|\s*`([a-z_]+)`\s*\|", section))


def _make_user(conn: psycopg.Connection, tenant_id: uuid.UUID) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (tenant_id, email, name, role) "
            "VALUES (%s, 'person@example.com', 'A Person', 'reviewer') RETURNING id",
            (tenant_id,),
        )
        return cur.fetchone()[0]


def _state_of(conn: psycopg.Connection, record_id: uuid.UUID) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT state FROM run_records WHERE id = %s", (record_id,))
        return cur.fetchone()[0]


def _commit_attempted_failed(conn: psycopg.Connection, ledger: Ledger, run) -> uuid.UUID:
    """A record that entered the commit window and then failed: commit_attempted is true."""
    record_id = ledger.add_record(run, state="submitting")
    conn.execute("UPDATE run_records SET state = 'failed' WHERE id = %s", (record_id,))
    return record_id


def _commit_attempted_parked(conn: psycopg.Connection, ledger: Ledger, run) -> uuid.UUID:
    """A commit-attempted record that was parked after reconcile: commit_attempted is true."""
    record_id = ledger.add_record(run, state="reconciling")
    conn.execute("UPDATE run_records SET state = 'parked' WHERE id = %s", (record_id,))
    return record_id


def _transition_count(conn: psycopg.Connection, record_id: uuid.UUID) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM run_record_transitions WHERE run_record_id = %s", (record_id,)
        )
        return int(cur.fetchone()[0])


def _input_record(conn: psycopg.Connection, ledger: Ledger, run) -> uuid.UUID:
    """One input record, so a forged ledger insert has a valid foreign key."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO input_records (tenant_id, batch_id, row_index, raw, record_key, status) "
            "SELECT %s, %s, coalesce(max(row_index), 0) + 1, '{}', 'input-1', 'ready' "
            "FROM input_records WHERE batch_id = %s RETURNING id",
            (ledger.tenant_id, run.batch_id, run.batch_id),
        )
        return cur.fetchone()[0]


# --- G1: a commit-attempted record never returns to pending without a destination check ----------


def test_g1_a_commit_attempted_failed_record_cannot_return_to_pending(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = _commit_attempted_failed(conn, ledger, run)

    with pytest.raises(psycopg.errors.RaiseException, match="destination_checked_by"):
        conn.execute("UPDATE run_records SET state = 'pending' WHERE id = %s", (record_id,))

    assert _state_of(conn, record_id) == "failed"


def test_g1_a_commit_attempted_parked_record_cannot_return_to_pending(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = _commit_attempted_parked(conn, ledger, run)

    with pytest.raises(psycopg.errors.RaiseException, match="destination_checked_by"):
        conn.execute("UPDATE run_records SET state = 'pending' WHERE id = %s", (record_id,))

    assert _state_of(conn, record_id) == "parked"


def test_g1_a_commit_attempted_failed_record_can_be_requeued_after_a_destination_check(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = _commit_attempted_failed(conn, ledger, run)
    user_id = _make_user(conn, ledger.tenant_id)

    conn.execute(
        "UPDATE run_records SET state = 'pending', destination_checked_by = %s, "
        "destination_checked_at = now() WHERE id = %s",
        (user_id, record_id),
    )

    assert _state_of(conn, record_id) == "pending"


def test_g1_a_commit_attempted_failed_record_can_go_to_reconciling(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = _commit_attempted_failed(conn, ledger, run)

    conn.execute("UPDATE run_records SET state = 'reconciling' WHERE id = %s", (record_id,))

    assert _state_of(conn, record_id) == "reconciling"


def test_g1_a_non_commit_failed_record_still_requeues_freely(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A record that never reached submitting needs no destination check: it may not have landed."""
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="failed")

    conn.execute("UPDATE run_records SET state = 'pending' WHERE id = %s", (record_id,))

    assert _state_of(conn, record_id) == "pending"


# --- G2: rows are born pending and are never deleted ---------------------------------------------


def test_g2_a_row_cannot_be_inserted_directly_as_verified(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    input_record_id = _input_record(conn, ledger, run)

    with pytest.raises(psycopg.errors.RaiseException, match="inserted only as pending"):
        conn.execute(
            "INSERT INTO run_records "
            "(tenant_id, run_id, input_record_id, record_key, state, idempotency_key) "
            "VALUES (%s, %s, %s, 'forged-1', 'verified', 'forged-1')",
            (ledger.tenant_id, run.id, input_record_id),
        )


def test_g2_a_record_cannot_be_deleted(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="submitting")

    with pytest.raises(psycopg.errors.RaiseException, match="DELETE is not allowed"):
        conn.execute("DELETE FROM run_records WHERE id = %s", (record_id,))

    assert _state_of(conn, record_id) == "submitting"


# --- G3: attempts and max_attempts are not writable by application code --------------------------


def test_g3_application_code_cannot_change_attempts(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="pending", attempts=2)

    with pytest.raises(
        psycopg.errors.RaiseException, match="attempts is written only by the trigger"
    ):
        conn.execute("UPDATE run_records SET attempts = 0 WHERE id = %s", (record_id,))


def test_g3_application_code_cannot_change_max_attempts(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, max_attempts=3)

    with pytest.raises(psycopg.errors.RaiseException, match="max_attempts is set at insert"):
        conn.execute("UPDATE run_records SET max_attempts = 99 WHERE id = %s", (record_id,))


# --- G4: a parked record can be dismissed -------------------------------------------------------


def test_g4_a_parked_record_can_be_dismissed_as_skipped(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="parked")

    conn.execute(
        "UPDATE run_records SET state = 'skipped', reason = 'dismissed by a person' WHERE id = %s",
        (record_id,),
    )

    assert _state_of(conn, record_id) == "skipped"


# --- G5: TRUNCATE cannot empty the append-only tables -------------------------------------------


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_g5_truncate_is_rejected(conn: psycopg.Connection, table: str) -> None:
    # CASCADE so a foreign-key restriction on the table does not preempt the trigger we are testing.
    with pytest.raises(psycopg.errors.RaiseException, match="TRUNCATE is not allowed"):
        conn.execute(f"TRUNCATE {table} CASCADE")


# --- G6: every state change writes an audit row -------------------------------------------------


def test_g6_a_state_change_writes_one_transition_row(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run)
    before = _transition_count(conn, record_id)

    conn.execute("UPDATE run_records SET state = 'claimed' WHERE id = %s", (record_id,))

    assert _transition_count(conn, record_id) == before + 1
    with conn.cursor() as cur:
        cur.execute(
            "SELECT from_state, to_state, actor FROM run_record_transitions "
            "WHERE run_record_id = %s ORDER BY id DESC LIMIT 1",
            (record_id,),
        )
        assert cur.fetchone()[0:2] == ("pending", "claimed")


def test_g6_the_bulk_recovery_update_writes_one_row_per_moved_record(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    for state in ("claimed", "prechecked", "filling"):
        ledger.add_record(run, state=state)
    for state in ("submitting", "submitted_unverified"):
        ledger.add_record(run, state=state)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM run_record_transitions WHERE tenant_id = %s", (ledger.tenant_id,)
        )
        before = int(cur.fetchone()[0])

        # The recovery updates from sql-patterns.md.
        cur.execute(
            "UPDATE run_records SET state = 'reconciling' "
            "WHERE run_id = %s AND state IN ('submitting', 'submitted_unverified')",
            (run.id,),
        )
        moved_to_reconciling = cur.rowcount
        cur.execute(
            "UPDATE run_records SET state = 'pending', claimed_by = NULL, claimed_at = NULL "
            "WHERE run_id = %s AND state IN ('claimed', 'prechecked', 'filling')",
            (run.id,),
        )
        moved_to_pending = cur.rowcount
        cur.execute(
            "SELECT count(*) FROM run_record_transitions WHERE tenant_id = %s", (ledger.tenant_id,)
        )
        after = int(cur.fetchone()[0])

    assert moved_to_reconciling == 2
    assert moved_to_pending == 3
    assert after - before == 5, "one audit row per moved record"


# --- G7: one active run per destination ---------------------------------------------------------


def test_g7_a_second_active_run_on_the_same_destination_is_rejected(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    ledger.new_run(status="running")

    with pytest.raises(psycopg.errors.UniqueViolation):
        ledger.new_run(status="running")


def test_g7_two_runs_may_be_queued_for_the_same_destination(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Only active runs are exclusive; queued runs are not."""
    ledger.new_run(status="running")
    ledger.new_run(status="queued")


def test_g7_a_run_requires_a_destination_key(conn: psycopg.Connection, ledger: Ledger) -> None:
    with pytest.raises(psycopg.errors.RaiseException, match="destination_key"):
        conn.execute(
            "INSERT INTO runs (tenant_id, workflow_id, recipe_version_id, mode) "
            "VALUES (%s, %s, %s, 'batch')",
            (ledger.tenant_id, ledger.workflow_id, ledger.recipe_version_id),
        )


# --- G8: a stale lease cannot write --------------------------------------------------------------


CONDITIONAL_STATE_CHANGE = """
UPDATE run_records
SET state = %(new_state)s
WHERE id = %(record)s
  AND state = %(expected)s
  AND claimed_by = %(worker)s
  AND EXISTS (SELECT 1 FROM runs WHERE id = run_records.run_id AND lease_epoch = %(epoch)s)
"""


def test_g8_a_stale_lease_epoch_updates_no_rows(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run(status="running")
    record_id = ledger.add_record(run, state="filling")
    conn.execute("UPDATE run_records SET claimed_by = 'worker-1' WHERE id = %s", (record_id,))
    conn.execute(
        "UPDATE runs SET worker_id = 'worker-2', lease_epoch = lease_epoch + 1 WHERE id = %s",
        (run.id,),
    )

    stale = conn.execute(
        CONDITIONAL_STATE_CHANGE,
        {
            "new_state": "pending",
            "record": record_id,
            "expected": "filling",
            "worker": "worker-1",
            "epoch": 0,
        },
    )
    live = conn.execute(
        CONDITIONAL_STATE_CHANGE,
        {
            "new_state": "pending",
            "record": record_id,
            "expected": "filling",
            "worker": "worker-1",
            "epoch": 1,
        },
    )

    assert stale.rowcount == 0, "a write carrying a stale lease updates nothing"
    assert live.rowcount == 1
    assert _state_of(conn, record_id) == "pending"


def test_g8_a_takeover_increments_the_lease_epoch(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run(status="running")
    conn.execute(
        "UPDATE runs SET heartbeat_at = now() - interval '10 minutes' WHERE id = %s", (run.id,)
    )

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE runs SET worker_id = 'worker-2', lease_epoch = lease_epoch + 1, "
            "heartbeat_at = now() "
            "WHERE id = %s AND status IN ('running', 'paused') "
            "AND heartbeat_at < now() - interval '1 minute' RETURNING lease_epoch",
            (run.id,),
        )
        assert cur.fetchone()[0] == 1


# --- G9: the idempotency key --------------------------------------------------------------------


def test_g9_a_row_without_an_idempotency_key_is_rejected(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    input_record_id = _input_record(conn, ledger, run)

    with pytest.raises(psycopg.errors.RaiseException, match="idempotency_key"):
        conn.execute(
            "INSERT INTO run_records (tenant_id, run_id, input_record_id, record_key) "
            "VALUES (%s, %s, %s, 'no-key-1')",
            (ledger.tenant_id, run.id, input_record_id),
        )


def test_g9_the_skip_pattern_ends_a_committed_record_skipped_not_verified(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(
        run, record_key="ravi@example.com", idempotency_key="ravi@example.com"
    )
    committed = ledger.add_record(run, state="verified", record_key="other@example.com")
    conn.execute(
        "INSERT INTO committed_keys "
        "(tenant_id, destination_key, idempotency_key, workflow_id, run_record_id) "
        "VALUES (%s, %s, 'ravi@example.com', %s, %s)",
        (ledger.tenant_id, DESTINATION_KEY, ledger.workflow_id, committed),
    )

    updated = conn.execute(
        "UPDATE run_records r SET state = 'skipped', "
        "reason = 'already committed in an earlier run' "
        "FROM committed_keys k "
        "WHERE r.run_id = %s AND r.state = 'pending' "
        "AND k.tenant_id = r.tenant_id AND k.destination_key = %s "
        "AND k.idempotency_key = r.idempotency_key",
        (run.id, DESTINATION_KEY),
    )

    assert updated.rowcount == 1
    assert _state_of(conn, record_id) == "skipped", "it ends skipped, never verified"


def test_g9_a_pending_record_cannot_be_marked_verified(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run)

    with pytest.raises(psycopg.errors.RaiseException, match="pending -> verified"):
        conn.execute("UPDATE run_records SET state = 'verified' WHERE id = %s", (record_id,))


# --- G10: approvals -----------------------------------------------------------------------------


def test_g10_an_approval_records_who_decided(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="submitting")
    user_id = _make_user(conn, ledger.tenant_id)

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO approvals "
            "(tenant_id, run_id, run_record_id, step_id, kind, shown, decision, decided_by) "
            "VALUES (%s, %s, %s, 'save-lead', 'first_commit', %s, 'approved', %s) "
            "RETURNING id, kind, decision, decided_by",
            (ledger.tenant_id, run.id, record_id, '{"record_key": "ravi@example.com"}', user_id),
        )
        approval_id, kind, decision, decided_by = cur.fetchone()

    assert (kind, decision, decided_by) == ("first_commit", "approved", user_id)


def test_g10_approvals_cannot_be_changed(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    user_id = _make_user(conn, ledger.tenant_id)
    approval_id = conn.execute(
        "INSERT INTO approvals (tenant_id, run_id, kind, decision, decided_by) "
        "VALUES (%s, %s, 'commit', 'approved', %s) RETURNING id",
        (ledger.tenant_id, run.id, user_id),
    ).fetchone()[0]

    with pytest.raises(psycopg.errors.RaiseException, match="approvals is append-only"):
        conn.execute("UPDATE approvals SET decision = 'rejected' WHERE id = %s", (approval_id,))
    with pytest.raises(psycopg.errors.RaiseException, match="approvals is append-only"):
        conn.execute("DELETE FROM approvals WHERE id = %s", (approval_id,))


# --- the transition matrix, read from the spec --------------------------------------------------


def test_the_spec_lists_eleven_states_and_thirty_one_transitions() -> None:
    assert len(_doc_states()) == 11
    assert len(_doc_transitions()) == 31


def test_the_transition_matrix_matches_the_ledger_spec(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    states = _doc_states()
    legal = _doc_transitions()
    run = ledger.new_run()
    mismatches: list[tuple[str, str, bool, bool]] = []

    for old_state in states:
        for new_state in states:
            record_id = ledger.add_record(run, state=old_state)
            expected_pass = old_state == new_state or (old_state, new_state) in legal
            try:
                conn.execute(
                    "UPDATE run_records SET state = %s WHERE id = %s", (new_state, record_id)
                )
                actual_pass = True
            except psycopg.errors.RaiseException:
                actual_pass = False
            if actual_pass != expected_pass:
                mismatches.append((old_state, new_state, expected_pass, actual_pass))

    assert mismatches == [], f"the guard and LEDGER_SPEC.md disagree: {mismatches}"


def test_every_state_is_reachable_from_pending(conn: psycopg.Connection, ledger: Ledger) -> None:
    """Each of the 11 states can be reached through legal transitions alone."""
    run = ledger.new_run()
    for state in _doc_states():
        record_id = ledger.add_record(run, state=state)
        assert _state_of(conn, record_id) == state
