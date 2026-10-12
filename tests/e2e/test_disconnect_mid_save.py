"""Disconnect, reconnect, and a runner crash mid-save.

The disconnect is made deterministic with the hub's own control
(``POST /api/v1/executor/sessions/{id}/close``), which detaches the extension
socket without ending the session: the extension reconnects with its resume
token, and the run continues against the same session.
"""

from __future__ import annotations

import pytest

from .conftest import NEW_EMAILS

pytestmark = pytest.mark.e2e

SLOW_SAVE_MS = 4000


def _row() -> dict[str, str]:
    return {"email": NEW_EMAILS[0], "interest": "Data course", "company": "Disc Co"}


def _wait_until_saving(harness) -> None:
    """Wait until the fixture reports the save landed inside the disconnect window."""
    _poll(lambda: harness.fixture_state()["disconnect_pending"], "the save never left a disconnect")


def _poll(predicate, message: str, timeout: float = 60.0) -> None:
    from .conftest import _wait_until

    _wait_until(predicate, timeout=timeout, message=message)


def test_a_disconnect_mid_save_parks_the_record_without_a_duplicate(harness) -> None:
    harness.set_switches(disconnect_mid_save=True, slow_save_ms=SLOW_SAVE_MS)
    email = NEW_EMAILS[0]
    run_id = harness.make_run([_row()])
    harness.start_worker(max_seconds=150)

    _wait_until_saving(harness)
    harness.close_session()

    harness.wait_for_run_detail(
        run_id, lambda detail: detail["pause_reason"] == "executor_disconnected", timeout=90
    )
    records = harness.api_get(f"/api/v1/runs/{run_id}/records")
    parked = [record for record in records if record["record_key"] == email]
    assert parked and parked[0]["state"] == "parked"
    assert harness.fixture_state()["leads"].get(email, 0) == 1, "no duplicate, and it did save once"


def test_reconnecting_and_resuming_continues_the_run(harness) -> None:
    harness.set_switches(disconnect_mid_save=True, slow_save_ms=SLOW_SAVE_MS)
    email = NEW_EMAILS[0]
    run_id = harness.make_run([_row()])
    harness.start_worker(max_seconds=180)

    _wait_until_saving(harness)
    harness.close_session()
    harness.wait_for_run_detail(
        run_id, lambda detail: detail["pause_reason"] == "executor_disconnected", timeout=90
    )

    # The extension reconnects on its own; the popup reports the session ready again.
    harness.popup.wait_for_function(
        "() => document.getElementById('session-state')?.dataset.state === 'ready'",
        timeout=30_000,
    )
    harness.api_post(f"/api/v1/runs/{run_id}/resume")
    harness.wait_for_terminal(run_id, timeout=180)

    assert harness.fixture_state()["leads"].get(email, 0) == 1


def test_a_runner_crash_and_takeover_parks_without_a_duplicate(harness) -> None:
    # A small heartbeat timeout so the crashed run is seen stale quickly; it must still
    # outlive 3x the longest step (the recipe's 8s assertion) plus the dispatch budget.
    harness.set_switches(slow_save_ms=8000)
    email = NEW_EMAILS[0]
    run_id = harness.make_run([_row()])
    crashed = harness.start_worker(
        worker_id="crashed",
        max_seconds=120,
        heartbeat_timeout=40.0,
        heartbeat_interval=1.0,
    )

    # The save reached the server, so the record is in its commit window; crash there.
    _poll(lambda: harness.fixture_state()["save_posts"] >= 1, "the save never reached the server")
    crashed.kill()
    crashed.wait(timeout=10)

    harness.start_worker(
        worker_id="takeover",
        max_seconds=240,
        heartbeat_timeout=40.0,
        heartbeat_interval=1.0,
    )
    records = harness.wait_for_terminal(run_id, timeout=240)

    parked = [record for record in records if record["record_key"] == email]
    assert parked and parked[0]["state"] == "parked", "an uncertain save is parked, never guessed"
    assert harness.fixture_state()["leads"].get(email, 0) == 1, "no duplicate after the takeover"
