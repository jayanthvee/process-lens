"""The ledger's legal transitions, mirrored in Python.

This is the same set the database trigger ``guard_record_transition()``
(``0003_run_orchestration.sql``) accepts. :mod:`packages.ledger` keeps it so the contract test can
compare the Python table with the 32 ordered pairs the database actually accepts, and so the runner
does not have to guess. The database is still the authority: this table is a mirror, not a second
source of truth.
"""

from __future__ import annotations

# 32 transitions: the 28 from 0001, plus parked>skipped, failed>reconciling and parked>reconciling
# from 0002, plus filling>skipped from 0003.
LEGAL_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
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
        ("filling", "verified"),
        ("filling", "parked"),
        ("filling", "failed"),
        ("filling", "pending"),
        ("filling", "skipped"),
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
    }
)

# An exit state: a record here is finished for this run and is not claimed again.
EXIT_STATES: frozenset[str] = frozenset({"verified", "skipped", "parked", "failed"})

# The commit window: the states in which the destination may already have the record.
COMMIT_WINDOW_STATES: frozenset[str] = frozenset(
    {"submitting", "submitted_unverified", "reconciling"}
)

# The states in which a worker holds a record and may still act on it.
WORKING_STATES: frozenset[str] = frozenset({"claimed", "prechecked", "filling"})
