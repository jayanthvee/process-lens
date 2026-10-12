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


def _poll(predicate, message: str, harness, run_id: str, timeout: float = 60.0) -> None:
    """Poll, and describe the run when it times out.

    A bare "the save never reached the server" cannot say whether the record was parked before the
    save, the worker died, or the run never started, and each of those needs a different fix.
    """
    from .conftest import _wait_until

    _wait_until(
        predicate,
        timeout=timeout,
        message=lambda: f"{message}: {harness.describe(run_id)}",
    )


def _wait_for_lead(harness, email: str, *, count: int = 1, timeout: float = 30.0) -> None:
    """Wait for the destination to hold this lead exactly `count` times.

    The save is driven by the browser, so it lands a little after the ledger settles; asserting
    immediately after the run would race the destination rather than test it.
    """
    from .conftest import _wait_until

    _wait_until(
        lambda: harness.fixture_state()["leads"].get(email, 0) == count,
        timeout=timeout,
        message=lambda: (
            f"{email!r} is {harness.fixture_state()['leads'].get(email, 0)} times, not {count}"
        ),
    )


def _wait_until_saving(harness, run_id: str) -> None:
    """Wait until the fixture reports the save landed inside the disconnect window."""
    _poll(
        lambda: harness.fixture_state()["disconnect_pending"],
        "the save never left a disconnect",
        harness,
        run_id,
    )


def test_a_disconnect_mid_save_parks_the_record_without_a_duplicate(harness) -> None:
    harness.set_switches(disconnect_mid_save=True, slow_save_ms=SLOW_SAVE_MS)
    email = NEW_EMAILS[0]
    run_id = harness.make_run([_row()])
    harness.start_worker(max_seconds=150)

    # Detach the moment the write-ahead has committed and the save is out: the disconnect lands
    # inside the commit window every time, rather than racing the fixture's own flag.
    _wait_until_in_the_commit_window(harness, run_id)
    harness.close_session()

    harness.wait_for_run_detail(
        run_id, lambda detail: detail["pause_reason"] == "executor_disconnected", timeout=90
    )
    records = harness.api_get(f"/api/v1/runs/{run_id}/records")
    parked = [record for record in records if record["record_key"] == email]
    assert parked and parked[0]["state"] == "parked"
    _wait_for_lead(harness, email, count=1)


def test_reconnecting_and_resuming_continues_the_run(harness) -> None:
    harness.set_switches(disconnect_mid_save=True, slow_save_ms=SLOW_SAVE_MS)
    email = NEW_EMAILS[0]
    run_id = harness.make_run([_row()])
    harness.start_worker(max_seconds=180)

    _wait_until_in_the_commit_window(harness, run_id)
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

    _wait_for_lead(harness, email, count=1)


def _wait_until_in_the_commit_window(harness, run_id: str) -> None:
    """Wait until the write-ahead has committed and the commit has been dispatched.

    This is the deterministic version of "crash the runner mid-save". Waiting for the fixture to
    notice a save is a race against the runner's own step budgets; waiting for the *ledger* to say
    `submitted_unverified` is exactly the moment the ticket describes, and it cannot be mistimed.
    """
    _poll(
        lambda: any(
            record["state"] == "submitted_unverified"
            for record in harness.api_get(f"/api/v1/runs/{run_id}/records")
        ),
        "the record never reached the commit window",
        harness,
        run_id,
    )


def test_a_runner_crash_and_takeover_parks_without_a_duplicate(harness) -> None:
    # A slow save keeps the destination busy while the runner dies; the heartbeat timeout must still
    # outlive 3x the longest step (the recipe's 8s assertion) plus the dispatch budget.
    harness.set_switches(slow_save_ms=4000)
    email = NEW_EMAILS[0]
    run_id = harness.make_run([_row()])
    crashed = harness.start_worker(
        worker_id="crashed",
        max_seconds=120,
        heartbeat_timeout=40.0,
        heartbeat_interval=1.0,
    )

    # Kill the worker the moment the record is in its commit window: the save is out, and whether
    # the destination took it is unknown.
    _wait_until_in_the_commit_window(harness, run_id)
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
    assert "could not verify" in (parked[0].get("reason") or ""), "parked by the parking reconciler"
    _wait_for_lead(harness, email, count=1)
