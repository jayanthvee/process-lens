"""The contract between ``packages.ledger`` and the database it mirrors (I13).

The Python transition table must be exactly the set the trigger accepts, and every pattern in
``internal/docs/sql-patterns.md`` must have a typed function here.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from packages import ledger as ledger_pkg
from tests.db.seed import STATE_PATHS, Ledger

# Every named pattern the ticket requires ``packages/ledger`` to implement.
REQUIRED_FUNCTIONS = (
    "claim_run",
    "take_over_run",
    "heartbeat",
    "claim_record",
    "change_state",
    "begin_commit",
    "mark_submitted_unverified",
    "verify_commit",
    "recover_run",
    "close_out_exhausted",
    "skip_already_committed",
    "requeue_record",
    "record_approval",
    "claim_job",
)


def test_every_sql_pattern_has_a_ledger_function() -> None:
    missing = [name for name in REQUIRED_FUNCTIONS if not callable(getattr(ledger_pkg, name, None))]
    assert missing == [], f"packages/ledger is missing: {missing}"


def test_the_public_surface_is_exported() -> None:
    for name in REQUIRED_FUNCTIONS:
        assert name in ledger_pkg.__all__, f"{name} must be part of the public surface"


def test_the_transition_table_has_thirty_two_entries() -> None:
    assert len(ledger_pkg.LEGAL_TRANSITIONS) == 32
    assert ("filling", "skipped") in ledger_pkg.LEGAL_TRANSITIONS


def _state(conn: psycopg.Connection, record_id: uuid.UUID) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT state FROM run_records WHERE id = %s", (record_id,))
        return cur.fetchone()[0]


@pytest.mark.parametrize("old_state", sorted(STATE_PATHS))
def test_the_python_table_matches_the_database(
    conn: psycopg.Connection, ledger: Ledger, old_state: str
) -> None:
    """For every ordered pair, the trigger accepts exactly what ``LEGAL_TRANSITIONS`` says."""
    mismatches: list[tuple[str, str, bool, bool]] = []
    for new_state in sorted(STATE_PATHS):
        if new_state == old_state:
            continue
        record_id = ledger.add_record(ledger.new_run(), state=old_state)
        expected = (old_state, new_state) in ledger_pkg.LEGAL_TRANSITIONS
        try:
            conn.execute("UPDATE run_records SET state = %s WHERE id = %s", (new_state, record_id))
            actual = True
        except psycopg.errors.RaiseException:
            actual = False
        if actual != expected:
            mismatches.append((old_state, new_state, expected, actual))
    assert mismatches == [], f"packages.ledger and the guard disagree: {mismatches}"
