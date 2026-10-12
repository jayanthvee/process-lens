"""PL-016b requirement 1: record on lead-crm, compile through the route, run the batch.

This is the chain a person actually uses, proved in one pass:

    the real popup starts a recording -> the demonstration is performed in the page ->
    the popup's Make recipe form compiles it through POST /api/v1/recipes/compile ->
    the compiled recipe is stored through POST /api/v1/recipes -> the real runner
    executes the existing five-row batch.

The hand-written ``fixtures/sites/lead-crm/recipe.json`` is used nowhere here: the recipe
under test is the one the recording compiled to.

The batch's expected shape is the ticket's: three `verified`, one `skipped`, one `parked`,
and each email saved exactly once in the fixture. The failure message carries every record's
state and reason, so a different outcome says what actually happened and why.
"""

from __future__ import annotations

from collections import Counter

import pytest

from .conftest import EXISTING_EMAIL, NEW_EMAILS, PARKED_EMAIL

pytestmark = pytest.mark.e2e

# One demonstration's values, synthetic on purpose. They become the sample row; the batch
# below supplies its own per-record values for the same columns.
DEMO_EMAIL = "demo@example.test"
DEMO_INTEREST = "Data course"
DEMO_COMPANY = "Demo Co"


def batch_rows() -> list[dict[str, str]]:
    """The five rows, the same set the hand-written recipe's batch uses."""
    return [
        {"email": EXISTING_EMAIL, "interest": DEMO_INTEREST, "company": "Existing Co"},
        {"email": NEW_EMAILS[0], "interest": DEMO_INTEREST, "company": "New One"},
        {"email": NEW_EMAILS[1], "interest": DEMO_INTEREST, "company": "New Two"},
        {"email": NEW_EMAILS[2], "interest": DEMO_INTEREST, "company": "New Three"},
        {"email": PARKED_EMAIL, "interest": DEMO_INTEREST, "company": ""},
    ]


def record_new_lead(harness) -> None:
    """Record New lead -> Email -> Interest -> Company -> Save, as a person would."""
    harness.start_recording()
    page = harness.page
    page.click("#new-lead-link")
    page.wait_for_load_state("load")
    page.fill("#lead-email", DEMO_EMAIL)
    page.select_option("#lead-interest", label=DEMO_INTEREST)
    page.fill("#lead-company", DEMO_COMPANY)
    page.click("#lead-save-button")
    # The save renders the confirmation on the page it lands on; wait for the recording to
    # carry that observation before Stop, so the commit compiles with its assertion.
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
    record_new_lead(harness)

    events = harness.recording_events()
    assert events, "the recorder captured nothing"
    # The extension is shared across the e2e session; fail with the reason if the events are not
    # this fixture's, rather than compiling a previous test's recording.
    assert any(harness.crm_url in (event.get("url") or "") for event in events), (
        f"the recording is not from the lead-crm fixture ({harness.crm_url}): "
        f"{[event.get('url') for event in events]}"
    )

    outcome = harness.compile_via_popup("New lead", record_key="email")
    assert outcome["recipe"] is not None, (
        "the Make recipe form could not compile the recording: "
        f"result={outcome['result']!r} warnings={outcome['warnings']!r} events={events}"
    )

    run_id = harness.make_run(batch_rows(), recipe=outcome["recipe"])
    records = harness.run_and_approve(run_id)

    counts = Counter(record["state"] for record in records)
    detail = describe(records)
    assert len(records) == 5, detail
    assert counts["verified"] == 3, detail
    assert counts["skipped"] == 1, detail
    assert counts["parked"] == 1, detail
    assert counts["failed"] == 0, detail

    leads = harness.fixture_state()["leads"]
    for email in (EXISTING_EMAIL, *NEW_EMAILS):
        assert leads.get(email, 0) == 1, f"{email!r} exists {leads.get(email, 0)} times ({detail})"
    assert leads.get(PARKED_EMAIL, 0) == 0, f"the parked record must not have saved ({detail})"
