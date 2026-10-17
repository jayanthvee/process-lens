"""PL-030: the demo supervisor's decision, as a pure function.

The supervisor polls two things -- the runs a worker could take, and the
extension session that is attached -- and decides whether to start its one
worker. The decision is a pure function over what the polls returned, so these
tests run no processes and touch no database.
"""

from __future__ import annotations

import uuid

from packages.ledger.models import OpenRunRow
from scripts.demo import START, WAIT, worker_decision


def run(status: str, *, worker_live: bool = False) -> OpenRunRow:
    """One polled run row; the id is irrelevant to the decision."""
    return OpenRunRow(run_id=uuid.uuid4(), status=status, worker_live=worker_live)


def test_a_queued_run_starts_a_worker() -> None:
    """A run nobody has claimed, and a browser to drive: start."""
    assert worker_decision([run("queued")], "session-1", worker_alive=False) == START


def test_a_running_run_whose_worker_died_starts_a_new_one() -> None:
    """The lease says nobody is driving it, so the supervisor steps in."""
    assert (
        worker_decision([run("running", worker_live=False)], "session-1", worker_alive=False)
        == START
    )


def test_a_running_run_with_a_live_worker_waits() -> None:
    """The run's own worker is alive; the supervisor must not race it."""
    assert (
        worker_decision([run("running", worker_live=True)], "session-1", worker_alive=False) == WAIT
    )


def test_a_paused_run_waits_for_a_person() -> None:
    """A paused run waits for a person, not for a worker."""
    assert worker_decision([run("paused")], "session-1", worker_alive=False) == WAIT


def test_nothing_to_do_waits() -> None:
    """No runs at all: nothing to start, whatever the session says."""
    assert worker_decision([], "session-1", worker_alive=False) == WAIT


def test_a_live_worker_of_our_own_waits() -> None:
    """Only one worker at a time, even with a queued run and a session ready."""
    assert worker_decision([run("queued")], "session-1", worker_alive=True) == WAIT


def test_no_extension_session_waits() -> None:
    """A worker with no browser to drive cannot claim: wait for the popup to connect."""
    assert worker_decision([run("queued")], None, worker_alive=False) == WAIT
