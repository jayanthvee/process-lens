"""An off-origin page, and a destructive control.

The off-origin guard parks the record when the bound tab is not on an allowed
origin; the destructive-control guard refuses a click on a control whose name says
delete, cancel, refund, pay or transfer, and the record parks. Neither ever acts,
and neither ever fails the record.
"""

from __future__ import annotations

import pytest

from .conftest import NEW_EMAILS, simple_recipe

pytestmark = pytest.mark.e2e


def test_an_off_origin_page_parks_the_record(harness) -> None:
    email = NEW_EMAILS[0]
    # The bound tab is left on the partner origin: the recipe's first command is
    # guarded there, and the CRM origin is the only allowed one.
    harness.page.goto(harness.partner_url + "/partner.html")
    run_id = harness.make_run([{"email": email, "interest": "Data course", "company": "Off Co"}])
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
