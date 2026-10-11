"""Typed errors for the ledger.

A ledger write never fails silently. A conditional state change that matches no row raises
:class:`StaleLease` (the caller no longer owns the record or its run lease) or
:class:`StateConflict` (the record was not in the state the caller expected). A refusal by the
database guard is surfaced as :class:`IllegalTransition`, so a caller can handle "the database
refused this write" without importing psycopg itself.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import psycopg


class LedgerError(Exception):
    """Base class for every ledger failure."""


class StaleLease(LedgerError):
    """A write matched no row because ``claimed_by``/``lease_epoch`` were stale."""


class StateConflict(LedgerError):
    """A write matched no row because the record was not in the expected state."""


class IllegalTransition(LedgerError):
    """The database guard rejected the write (an illegal transition, or a locked column changed)."""


@contextlib.contextmanager
def guard_refusal_is_typed() -> Iterator[None]:
    """Re-raise a database guard refusal as :class:`IllegalTransition`.

    The guard signals a refusal with ``RAISE EXCEPTION``, which psycopg surfaces as a raw
    :class:`psycopg.errors.RaiseException`. A caller that catches :class:`LedgerError` should see
    the ledger's own type, so the runner can decide what to do without knowing psycopg.
    """
    try:
        yield
    except psycopg.errors.RaiseException as exc:
        raise IllegalTransition(str(exc)) from exc
