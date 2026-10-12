"""PL-016b requirement 2: the golden test on the messy-crm fixture.

The flow is the one the ticket names, recorded through the real popup:

    add two recipients and remove one with its cross ->
    type into the rich-text notes -> visit the second page and come back with Back -> Save.

Then it compiles through the route and runs three rows. The pass condition is the ticket's: all
three rows `verified`; the fixture shows each saved record with exactly the recipients the row
named, never the removed one; the notes text is the row's text; and the compiled recipe contains a
`navigate` step for the Back.

If a step cannot be recorded or replayed, the test fails with the reason, and the report names the
PL-016 item it traces to. Nothing here is papered over.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

DEMO_KEPT = "bob@example.test"
DEMO_REMOVED = "carol@example.test"
DEMO_NOTE = "Called on Monday"


def messy_rows() -> list[dict[str, str]]:
    """Three rows: a kept recipient per row, the same one removed, and the row's note.

    The demolition records adding two recipients and removing the second, so the column the
    recording bound the first entry to carries the kept address and the second carries the one the
    cross removes.
    """
    return [
        {
            "add-recipient": "dana@example.test",
            "add-recipient-2": DEMO_REMOVED,
            "notes": "Note one",
        },
        {
            "add-recipient": "erin@example.test",
            "add-recipient-2": DEMO_REMOVED,
            "notes": "Note two",
        },
        {
            "add-recipient": "frank@example.test",
            "add-recipient-2": DEMO_REMOVED,
            "notes": "Note three",
        },
    ]


def record_messy(harness) -> None:
    """Record the messy demonstration exactly as the ticket describes it."""
    harness.start_recording()
    page = harness.page

    # Two recipients, then remove the second with its cross.
    page.fill('[data-testid="recipient-input"]', DEMO_KEPT)
    page.click('[data-testid="recipient-add"]')
    page.fill('[data-testid="recipient-input"]', DEMO_REMOVED)
    page.click('[data-testid="recipient-add"]')
    page.click(f'button[aria-label="Remove {DEMO_REMOVED}"]')

    # Type into the rich-text notes.
    page.click('[data-testid="notes-box"]')
    page.keyboard.type(DEMO_NOTE)

    # Visit the second page and come back with Back.
    page.click('[data-testid="nav-second"]')
    page.wait_for_load_state("load")
    page.go_back()
    page.wait_for_load_state("load")

    # Save; the next page renders the confirmation.
    page.click('[data-testid="save"]')
    page.wait_for_selector("text=Saved", timeout=10_000)
    assert harness.wait_for_observation(), "the save's confirmation was never recorded"
    harness.stop_recording()


def test_record_compile_run_on_messy_crm(messy_harness) -> None:
    messy_harness.messy_reset()
    messy_harness.page.goto(messy_harness.crm_url + "/")
    record_messy(messy_harness)

    events = messy_harness.recording_events()
    assert events, "the recorder captured nothing"
    # The extension is shared with the lead-crm harness in one pytest session. If a later test
    # inherits a wedged extension, its recording can be the previous test's; fail with the reason
    # rather than compiling someone else's recording.
    assert any(messy_harness.crm_url in (event.get("url") or "") for event in events), (
        f"the recording is not from the messy fixture ({messy_harness.crm_url}): "
        f"{[event.get('url') for event in events]}"
    )

    outcome = messy_harness.compile_via_popup("Messy compose", record_key="notes")
    assert outcome["recipe"] is not None, (
        "the Make recipe form could not compile the recording: "
        f"result={outcome['result']!r} warnings={outcome['warnings']!r} events={events}"
    )
    recipe = outcome["recipe"]

    # The compiled recipe must carry the Back as a navigate step (PL-016 C1).
    navigate_steps = [step for step in recipe["steps"] if step.get("action") == "navigate"]
    assert len(navigate_steps) >= 2, (
        "the compiled recipe has no navigate step for the Back: "
        f"{[step.get('action') for step in recipe['steps']]}"
    )

    run_id = messy_harness.make_run(messy_rows(), recipe=recipe)
    records = messy_harness.run_and_approve(run_id)

    detail = "; ".join(
        f"{record.get('record_key')}={record['state']}:{record.get('reason')}" for record in records
    )
    assert len(records) == 3, detail
    assert all(record["state"] == "verified" for record in records), detail

    state = messy_harness.messy_state()
    for row in messy_rows():
        kept = row["add-recipient"]
        assert state["recipients"].get(kept, 0) == 1, f"{kept!r} not saved once ({detail})"
        assert state["recipients"].get(DEMO_REMOVED, 0) == 0, (
            f"the removed recipient {DEMO_REMOVED!r} was saved ({detail})"
        )
    assert state["notes"] == [row["notes"] for row in messy_rows()], state["notes"]
