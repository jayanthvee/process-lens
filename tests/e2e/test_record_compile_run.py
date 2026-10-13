"""PL-016b requirement 1, restated by T1's Ruling 1.

The chain a person uses, proved in one pass:

    the real popup starts a recording -> the demonstration is performed in the page ->
    the popup's Make recipe form compiles it through POST /api/v1/recipes/compile ->
    the compiled recipe is stored through POST /api/v1/recipes -> the real runner
    executes the batch.

The hand-written ``fixtures/sites/lead-crm/recipe.json`` is used nowhere here: the recipe under
test is the one the recording compiled to.

**The restated pass condition (Ruling 1).** A compiled recipe is linear by design — a person adds
duplicate-search branches and input rules in the workflow editor, which does not exist yet — so the
original 3/1/1 expectation was wrong. What this test proves is the safety property that still
holds without a branch: an existing lead is never created twice. The rows are ordered so the first
row is a new lead; the three new rows verify; the pre-existing lead and the row missing a value the
workflow needs each end **parked, with a reason**; nothing is `failed`; and the fixture's own state
shows each email exactly once, the pre-existing lead included.

**The canary.** The ledger makes the *first record to reach a commit* the run's canary, and a
parking canary pauses the run `canary_failed` — designed behaviour, not touched here. The ledger
claims ready records in an order that is not the row order (the rows share one `created_at` and
tie-break on a random uuid), so which record is the canary is not knowable in advance;
``Harness.settle_run`` clears the designed pause by resuming, exactly as the operator would.

The parked row is the one whose *company* is empty (the fourth column of the demonstration). A row
with no email cannot be used: the record key renders from the email (`{{row.email | lower}}`), so an
empty email is refused at run creation with no run at all (see PL-016's report). That is why the
"missing" row here is missing its company, not its email.
"""

from __future__ import annotations

from collections import Counter

import pytest

from .conftest import EXISTING_EMAIL, NEW_EMAILS

pytestmark = pytest.mark.e2e

# One demonstration's values, synthetic on purpose: they become the sample row.
DEMO_EMAIL = "demo@example.test"
DEMO_INTEREST = "Data course"
DEMO_COMPANY = "Demo Co"

# The row that parks: an email (so the key renders) but no company, which the compiled recipe's
# unconditional company fill refuses rather than submitting.
NO_COMPANY_EMAIL = "el@example.test"


def batch_rows() -> list[dict[str, str]]:
    """The five rows, ordered so the first row is a new lead (the canary the ticket asks for)."""
    return [
        {"email": NEW_EMAILS[0], "interest": DEMO_INTEREST, "company": "New One"},
        {"email": NEW_EMAILS[1], "interest": DEMO_INTEREST, "company": "New Two"},
        {"email": NEW_EMAILS[2], "interest": DEMO_INTEREST, "company": "New Three"},
        {"email": EXISTING_EMAIL, "interest": DEMO_INTEREST, "company": "Existing Co"},
        {"email": NO_COMPANY_EMAIL, "interest": DEMO_INTEREST, "company": ""},
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
    records = harness.settle_run(run_id)
    detail = describe(records)

    counts = Counter(record["state"] for record in records)
    assert len(records) == 5, detail
    assert counts["verified"] == 3, detail
    assert counts["failed"] == 0, detail

    # The pre-existing lead and the no-company row each park, with a reason a person can read.
    by_key = {record["record_key"]: record for record in records}
    assert by_key[EXISTING_EMAIL]["state"] == "parked", detail
    assert by_key[EXISTING_EMAIL]["reason"], f"a park must carry a reason ({detail})"
    assert by_key[NO_COMPANY_EMAIL]["state"] == "parked", detail
    assert by_key[NO_COMPANY_EMAIL]["reason"], f"a park must carry a reason ({detail})"

    # The safety property: nothing was created twice, and the parked row created nothing.
    leads = harness.fixture_state()["leads"]
    for email in (*NEW_EMAILS, EXISTING_EMAIL):
        assert leads.get(email, 0) == 1, f"{email!r} exists {leads.get(email, 0)} times ({detail})"
    assert leads.get(NO_COMPANY_EMAIL, 0) == 0, f"the parked row must not save ({detail})"
