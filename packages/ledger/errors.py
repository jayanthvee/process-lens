"""Typed errors for the ledger.

A ledger write never fails silently. A conditional state change that matches no row raises
:class:`StaleLease` (the caller no longer owns the record or its run lease) or
:class:`StateConflict` (the record was not in the state the caller expected). The database guard's
own exception is re-raised as :class:`IllegalTransition`.
"""

from __future__ import annotations


class LedgerError(Exception):
    """Base class for every ledger failure."""


class StaleLease(LedgerError):
    """A write matched no row because ``claimed_by``/``lease_epoch`` were stale."""


class StateConflict(LedgerError):
    """A write matched no row because the record was not in the expected state."""


class IllegalTransition(LedgerError):
    """The database guard rejected the state change."""
