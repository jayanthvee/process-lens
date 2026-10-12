"""An off-origin page, and a destructive control.

The off-origin guard parks the record when a step acts on a page that is not an allowed origin.
The opening `navigate` is the exception: its own URL is checked against `allowed_origins`, and the
guards run on the page it lands on, so a permitted navigate is never refused for the page the tab
was leaving (PL-014b item 5). The destructive-control guard refuses a click on a control whose name
says delete, cancel, refund, pay or transfer, and the record parks. Neither ever acts, and neither
ever fails the record.
"""

from __future__ import annotations

import pytest

from .conftest import NEW_EMAILS, simple_recipe

pytestmark = pytest.mark.e2e


def test_an_opening_navigate_leaves_an_off_origin_page(harness) -> None:
    """Item 5: the bound tab is left on the partner origin, and the opening navigate still runs.

    The recipe's first step is a `navigate` to the CRM, whose URL is allowed. The guards for it run
    on the page it lands on, not on the partner page the tab was leaving, so the record completes.
    """
    email = NEW_EMAILS[0]
    harness.page.goto(harness.partner_url + "/partner.html")
    run_id = harness.make_run([{"email": email, "interest": "Data course", "company": "Off Co"}])
    harness.start_worker(max_seconds=120)
    records = harness.wait_for_terminal(run_id, timeout=120)

    assert records[0]["state"] == "verified", (
        "a permitted navigate is not judged by the page it leaves"
    )
    assert harness.fixture_state()["leads"].get(email, 0) == 1


def test_a_step_on_an_off_origin_page_parks_the_record(harness) -> None:
    """The guard's own case: a step that ACTS on an off-origin page is refused.

    No opening navigate, so the first thing the recipe does is the click - and the tab is on the
    partner origin, which is not allowed. The record parks and nothing is created.
    """
    email = NEW_EMAILS[0]
    harness.page.goto(harness.partner_url + "/partner.html")
    recipe = simple_recipe(
        [
            {
                "id": "off-origin-click",
                "action": "click",
                "targets": [{"by": "css", "selector": "#save"}],
            }
        ],
        origin=harness.crm_url,
    )
    run_id = harness.make_run(
        [{"email": email, "interest": "Data course", "company": "Off Co"}], recipe=recipe
    )
    harness.start_worker(max_seconds=120)
    records = harness.wait_for_terminal(run_id, timeout=120)

    assert records[0]["state"] == "parked"
    assert records[0]["reason"], "a park carries the reason for a person"
    assert harness.fixture_state()["leads"].get(email, 0) == 0


def test_a_destructive_control_parks_the_record(harness) -> None:
    email = NEW_EMAILS[0]
    harness.page.goto(harness.crm_url + "/new-lead.html")
    # A real control on a real page, whose accessible name says delete.
    harness.page.evaluate(
        "() => {"
        " const button = document.createElement('button');"
        " button.id = 'danger';"
        " button.setAttribute('role', 'button');"
        " button.textContent = 'Delete lead';"
        " document.body.appendChild(button);"
        "}"
    )
    recipe = simple_recipe(
        [
            {
                "id": "delete-lead",
                "action": "click",
                "targets": [{"by": "css", "selector": "#danger"}],
            }
        ],
        origin=harness.crm_url,
    )
    run_id = harness.make_run(
        [{"email": email, "interest": "Data course", "company": "Danger Co"}], recipe=recipe
    )
    harness.start_worker(max_seconds=120)
    records = harness.wait_for_terminal(run_id, timeout=120)

    assert records[0]["state"] == "parked", "a refused destructive click parks, it never fails"
    assert harness.fixture_state()["leads"].get(email, 0) == 0
