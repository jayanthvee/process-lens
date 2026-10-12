"""A challenge marker, and a login wall.

The wall is raised by the save itself: the ``challenge_page`` and ``login_page`` switches make
``POST /leads/new`` answer the wall instead of storing the lead (fixture README, drift matrix). The
tab therefore starts on the CRM like any other case, and the run has to discover the wall where it
actually appears - after the commit, in the post-save check. The extension probes the page when a
check ends false, and the run pauses with the wall's own reason instead of letting the assertion
time out into ``canary_failed``. No record may be ``failed``: a wall is a pause for a person,
never a failure.
"""

from __future__ import annotations

import pytest

from .conftest import NEW_EMAILS

pytestmark = pytest.mark.e2e


def _row() -> dict[str, str]:
    return {"email": NEW_EMAILS[0], "interest": "Data course", "company": "Wall Co"}


def _parked(harness, run_id: str) -> dict[str, object]:
    """The record for this case, which must be parked - never failed."""
    records = harness.api_get(f"/api/v1/runs/{run_id}/records")
    assert records, "the run has no records"
    record = records[0]
    assert record["state"] == "parked", f"a wall parks the record, never {record['state']}"
    assert harness.fixture_state()["leads"].get(NEW_EMAILS[0], 0) == 0
    return record


@pytest.mark.e2e
def test_a_challenge_page_pauses_the_run_with_challenge(harness) -> None:
    harness.set_switches(challenge_page=True)
    run_id = harness.make_run([_row()])
    harness.start_worker(max_seconds=120)

    detail = harness.wait_for_run_detail(
        run_id, lambda body: body["pause_reason"] == "challenge", timeout=90
    )
    assert detail["status"] == "paused"
    _parked(harness, run_id)


@pytest.mark.e2e
def test_a_login_page_pauses_the_run_with_login_required(harness) -> None:
    harness.set_switches(login_page=True)
    run_id = harness.make_run([_row()])
    harness.start_worker(max_seconds=120)

    detail = harness.wait_for_run_detail(
        run_id, lambda body: body["pause_reason"] == "login_required", timeout=90
    )
    assert detail["status"] == "paused"
    _parked(harness, run_id)
