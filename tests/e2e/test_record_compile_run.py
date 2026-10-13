"""PL-017 requirement 6: the golden test on lead-crm, back to 3/1/1.

The chain a person uses: search for the email -> the results say "No leads found" -> New lead ->
Email -> Interest -> Company -> Save. The demonstration compiles through the popup's Make recipe
form, which leaves the duplicate check on and marks Company required. The recipe it makes is no
longer linear: it carries the compiler's built-in rules.

* A duplicate-search branch ends a record ``skipped`` when the search already shows its key -- the
  existing lead ``ana``.
* A precheck rule parks a record whose company is empty, before the browser is touched.

The five rows are ordered row 0..4, and the claim query (PL-017 requirement 2) claims by the input
row, so row 0 -- a new lead -- is the first record to reach a commit and the canary is
deterministic: three ``verified``, ``ana`` ``skipped`` with reason "duplicate", the no-company row
``parked`` with reason "missing company", nothing ``failed``, and each email exists exactly once.
"""

from __future__ import annotations

import json
import os
from collections import Counter

import pytest

from .conftest import EXISTING_EMAIL, NEW_EMAILS

pytestmark = pytest.mark.e2e

# One demonstration's values, synthetic on purpose: they become the sample row.
DEMO_EMAIL = "demo@example.test"
DEMO_INTEREST = "Data course"
DEMO_COMPANY = "Demo Co"

# The row that parks: an email (so the key renders), but no company, which the park rule refuses.
NO_COMPANY_EMAIL = "el@example.test"


# The record key column, as the Make recipe form defaults it. The demonstration types the email in
# the search box first, so that field's column ("Search leads" -> ``search_leads``) is the column
# that holds the email; recordedValues keeps one column per value, and the create's own email fill
# reads the same column. The batch's email goes under this column.
KEY_COLUMN = "search_leads"


def batch_rows() -> list[dict[str, str]]:
    """The five rows, ordered so the first row is a new lead (the canary the ticket asks for)."""
    return [
        {KEY_COLUMN: NEW_EMAILS[0], "interest": DEMO_INTEREST, "company": "New One"},
        {KEY_COLUMN: NEW_EMAILS[1], "interest": DEMO_INTEREST, "company": "New Two"},
        {KEY_COLUMN: NEW_EMAILS[2], "interest": DEMO_INTEREST, "company": "New Three"},
        {KEY_COLUMN: EXISTING_EMAIL, "interest": DEMO_INTEREST, "company": "Existing Co"},
        {KEY_COLUMN: NO_COMPANY_EMAIL, "interest": DEMO_INTEREST, "company": ""},
    ]


def record_search_then_new_lead(harness) -> None:
    """Search for the email (a miss), then New lead -> Email -> Interest -> Company -> Save."""
    harness.start_recording()
    page = harness.page
    # The search the duplicate check reads: a miss, so the results say "No leads found".
    page.fill("#lead-search", DEMO_EMAIL)
    page.click("#lead-search-button")
    page.wait_for_selector("text=No leads found", timeout=10_000)
    # From the results, create the lead.
    page.click("#new-lead-link")
    page.wait_for_load_state("load")
    page.fill("#lead-email", DEMO_EMAIL)
    page.select_option("#lead-interest", label=DEMO_INTEREST)
    page.fill("#lead-company", DEMO_COMPANY)
    page.click("#lead-save-button")
    # The save renders the confirmation on the page it lands on; wait for the recording to carry
    # that observation before Stop, so the commit compiles with its assertion.
    page.wait_for_selector("text=Lead created", timeout=10_000)
    assert harness.wait_for_observation(), "the save's confirmation was never recorded"
    harness.stop_recording()


def describe(records: list[dict]) -> str:
    return "; ".join(
        f"{record.get('record_key')}={record['state']}:{record.get('reason')}" for record in records
    )


def test_record_compile_run_on_lead_crm(harness) -> None:
    harness.reset_fixture()
    harness.seed_lead(EXISTING_EMAIL)
    harness.settle_page()
    record_search_then_new_lead(harness)

    events = harness.recording_events()
    assert events, "the recorder captured nothing"
    # The extension is shared across the e2e session; fail with the reason if the events are not
    # this fixture's, rather than compiling a previous test's recording.
    assert any(harness.crm_url in (event.get("url") or "") for event in events), (
        f"the recording is not from the lead-crm fixture ({harness.crm_url}): "
        f"{[event.get('url') for event in events]}"
    )

    # Keep the defaults: the duplicate check is on (a search was recorded), and Company is marked
    # required by ticking its park checkbox in the Make recipe form.
    outcome = harness.compile_via_popup("New lead", park_fields=["company"])
    assert outcome["recipe"] is not None, (
        "the Make recipe form could not compile the recording: "
        f"result={outcome['result']!r} warnings={outcome['warnings']!r} events={events}"
    )
    recipe = outcome["recipe"]
    assert recipe["meta"]["record_key"] == f"{{{{row.{KEY_COLUMN} | lower}}}}", recipe["meta"]
    if os.environ.get("PROCESSLENS_E2E_DUMP_RECIPE") == "1":
        print("COMPILED RECIPE:", json.dumps(recipe, indent=2, sort_keys=True))

    steps = recipe["steps"]
    assert any(step.get("action") == "branch" for step in steps), steps
    rules = recipe["meta"]["input_schema"].get("rules", [])
    assert {"when": {"missing": "company"}, "then": "park", "reason": "missing company"} in rules, (
        rules
    )

    run_id = harness.make_run(batch_rows(), recipe=recipe)
    records = harness.settle_run(run_id)
    detail = describe(records)

    counts = Counter(record["state"] for record in records)
    assert len(records) == 5, detail
    assert counts["verified"] == 3, detail
    assert counts["skipped"] == 1, detail
    assert counts["parked"] == 1, detail
    assert counts["failed"] == 0, detail

    by_key = {record["record_key"]: record for record in records}
    assert by_key[EXISTING_EMAIL]["state"] == "skipped", detail
    assert by_key[EXISTING_EMAIL]["reason"] == "duplicate", detail
    assert by_key[NO_COMPANY_EMAIL]["state"] == "parked", detail
    assert by_key[NO_COMPANY_EMAIL]["reason"] == "missing company", detail

    # The safety property: nothing was created twice, and the parked row created nothing.
    leads = harness.fixture_state()["leads"]
    for email in (*NEW_EMAILS, EXISTING_EMAIL):
        assert leads.get(email, 0) == 1, f"{email!r} exists {leads.get(email, 0)} times ({detail})"
    assert leads.get(NO_COMPANY_EMAIL, 0) == 0, f"the parked row must not save ({detail})"
