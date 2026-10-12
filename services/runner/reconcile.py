"""Reconcile: check the destination before retrying a record whose commit outcome is unknown.

The recipe schema has no reconcile definition yet (the ticket's open decision), so the runner works
against this interface. This ticket ships :class:`NoopReconciler` for tests that never reach it,
and a fake in ``testing.py``; the real one arrives with the schema change.

Outcomes: exactly one match means the commit landed (``verified``); none means retry (``pending``);
several matches, or a search error, means a person must look (``parked``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from packages.ledger import RecordClaim

ReconcileOutcome = Literal["verified", "pending", "parked"]


@dataclass(frozen=True)
class ReconciledDiff:
    """What a destination search found for one record."""

    matches: int
    destination_ref: str | None = None
    error: str | None = None


class Reconciler(Protocol):
    """The seam the runner reconciles through."""

    def search(self, record: RecordClaim) -> ReconciledDiff:
        """Look for this record's commit in the destination."""
        ...


class NoopReconciler:
    """A reconciler that finds nothing, for tests that never reach the destination check."""

    def search(self, record: RecordClaim) -> ReconciledDiff:
        return ReconciledDiff(matches=0)


# PL-014: the reason a record is parked because nobody could check the destination.
PARKING_REASON = "could not verify the save automatically; check the destination"


class ParkingReconciler:
    """The reconciler for a real run until P-003 is decided (PL-014).

    A real run has no reconcile definition to run (the recipe format has none yet), so there is no
    way to prove the destination does *not* have the record. It therefore always reports an error,
    and :func:`outcome_of` maps an error to ``parked`` — never to ``pending``. "Not found" would
    prove nothing, so a person checks the destination.
    """

    def search(self, record: RecordClaim) -> ReconciledDiff:
        return ReconciledDiff(matches=0, error=PARKING_REASON)


def outcome_of(diff: ReconciledDiff) -> ReconcileOutcome:
    """Turn a search result into a ledger outcome (S2: an error parks, it never fails)."""
    if diff.error is not None:
        return "parked"
    if diff.matches == 1:
        return "verified"
    if diff.matches == 0:
        return "pending"
    return "parked"
