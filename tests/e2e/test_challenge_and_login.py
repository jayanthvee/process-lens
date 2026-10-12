"""A challenge marker, and a login wall.

The guards run before every command against the page the extension is bound to, so
the deterministic trigger is to leave the bound tab on the wall page: the recipe's
first command is guarded and the run pauses with the reason the wall produced. No
record may be ``failed`` - a wall is a pause for a person, never a failure.
"""

from __future__ import annotations

import pytest

from .conftest import NEW_EMAILS

pytestmark = pytest.mark.e2e


def _row() -> dict[str, str]:
    return {"email": NEW_EMAILS[0], "interest": "Data course", "company": "Wall Co"}


@pytest.mark.e2e
def test_a_challenge_page_pauses_the_run_with_challenge(harness) -> None:
    harness.set_switches(challenge_page=True)
    harness.page.goto(f"{harness.crm_url}/challenge.html")
    run_id = harness.make_run([_row()])
    harness.start_worker(max_seconds=120)

    detail = harness.wait_for_run_detail(
        run_id, lambda body: body["pause_reason"] == "challenge", timeout=90
    )
    assert detail["status"] == "paused"
    records = harness.api_get(f"/api/v1/runs/{run_id}/records")
    assert all(record["state"] != "failed" for record in records)
    assert harness.fixture_state()["leads"].get(NEW_EMAILS[0], 0) == 0


@pytest.mark.e2e
def test_a_login_page_pauses_the_run_with_login_required(harness) -> None:
    harness.set_switches(login_page=True)
    harness.page.goto(f"{harness.crm_url}/login.html")
    run_id = harness.make_run([_row()])
    harness.start_worker(max_seconds=120)

    detail = harness.wait_for_run_detail(
        run_id, lambda body: body["pause_reason"] == "login_required", timeout=90
    )
    assert detail["status"] == "paused"
    records = harness.api_get(f"/api/v1/runs/{run_id}/records")
    assert all(record["state"] != "failed" for record in records)
    assert harness.fixture_state()["leads"].get(NEW_EMAILS[0], 0) == 0
