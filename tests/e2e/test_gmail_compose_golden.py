"""PL-018 requirement 6: the Gmail-style compose golden.

The demonstration is the one the ticket names, recorded through the real popup against the synthetic
widget-corpus fixture (real Gmail is never touched):

    Compose -> To via keyboard autofill -> body text -> Send.

Then it compiles through the route and runs three rows. The pass condition is the ticket's: each
row's message is sent exactly once, to that row's address only, with that row's body, and the
fixture's own state proves it.

This is the case the recorder used to lose: the body editor fires no `input` event (the page writes
`textContent` itself), the recipient accepted with the keyboard was recorded as the typed prefix
"ma", and a suggestion clicked was recorded *before* the typing that produced it.
"""

from __future__ import annotations

import json
import os

import pytest

from .conftest import simple_recipe

pytestmark = pytest.mark.e2e

DEMO_PREFIX = "dana"
DEMO_ADDRESS = "dana@example.test"
DEMO_BODY = "Hello from the demonstration"

BODY_COLUMN_HINTS = ("body", "message")
ADDRESS_COLUMN_HINTS = ("to", "email", "recipient")


def golden_rows() -> list[dict[str, str]]:
    """Three rows: a distinct address and a distinct body each."""
    return [
        {"to": "dana@example.test", "message_body": "Row one body"},
        {"to": "erin@example.test", "message_body": "Row two body"},
        {"to": "frank@example.test", "message_body": "Row three body"},
    ]


def warm_up_corpus(harness) -> None:
    """Run one trivial batch so the first real command is not the session's cold one.

    ``warm_up`` in conftest performs this for the lead-crm harness; the corpus tab needs its own,
    because it is a different origin and the first command against a fresh tab is the slowest.
    """
    recipe = simple_recipe(
        [
            {"id": "warm-open", "action": "navigate", "url": f"{harness.crm_url}/"},
            {"id": "warm-end", "action": "end_record", "outcome": "skipped", "reason": "warm-up"},
        ],
        origin=harness.crm_url,
    )
    run_id = harness.make_run([{"email": "warmup@example.test"}], recipe=recipe)
    harness.start_worker(worker_id="corpus-warm-up", max_seconds=90)
    try:
        harness.wait_for_terminal(run_id, timeout=90.0)
    finally:
        harness.stop_workers()


def record_demo(harness) -> None:
    """Record the ticket's demonstration exactly as a person would."""
    page = harness.page
    harness.start_recording()

    page.click('[data-testid="compose"]')
    page.click('[data-testid="to"]')
    page.keyboard.type(DEMO_PREFIX)
    page.wait_for_selector('[data-testid="suggestions"] [role="option"]', timeout=5_000)
    # Accept the suggestion with the keyboard: the case the old recorder turned into "ma".
    page.keyboard.press("Enter")

    # The body editor fires no input event; only the state diff can see this.
    page.click('[data-testid="body-editor"]')
    page.keyboard.type(DEMO_BODY)

    page.click('[data-testid="send"]')
    page.wait_for_selector('[data-testid="undo-toast"]', timeout=10_000)
    assert harness.wait_for_observation(), "the Send's confirmation was never recorded"
    harness.stop_recording()


def _row_from_sample(sample: dict[str, str], address: str, body: str) -> dict[str, str]:
    """Build one run row from the compiled sample row, by column-name hints.

    The popup derives the column names from the recorded field names, so the test reads them back
    rather than spelling them: an address-shaped column gets the row's address, a body-shaped one
    the row's body, and anything else keeps the recorded value (the fixture's other fields are
    constants for this ticket).
    """
    row: dict[str, str] = {}
    for column, value in sample.items():
        lowered = column.lower()
        if any(hint in lowered for hint in ADDRESS_COLUMN_HINTS) or "@" in str(value):
            row[column] = address
        elif any(hint in lowered for hint in BODY_COLUMN_HINTS):
            row[column] = body
        else:
            row[column] = value
    return row


def test_the_gmail_style_compose_records_and_replays_three_rows(corpus_harness) -> None:
    harness = corpus_harness
    harness.corpus_reset()
    warm_up_corpus(harness)

    harness.page.goto(harness.crm_url + "/gmail-compose.html")
    record_demo(harness)

    events = harness.recording_events()
    assert events, "the recorder captured nothing"
    assert any(harness.crm_url in (event.get("url") or "") for event in events), (
        f"the recording is not from the corpus fixture ({harness.crm_url}): "
        f"{[event.get('url') for event in events]}"
    )
    # The body must be in the recording: it is the field no input event reports.
    recorded_values = [
        str((event.get("step") or {}).get("value"))
        for event in events
        if event.get("kind") == "fill"
    ]
    assert any(DEMO_BODY in value for value in recorded_values), (
        f"the body was never recorded: {recorded_values!r}"
    )
    # And the recipient must be the address, never the typed prefix.
    assert any(DEMO_ADDRESS in value for value in recorded_values), (
        f"the recipient was not recorded as the chip's address: {recorded_values!r}"
    )
    assert f"'value': '{DEMO_PREFIX}'" not in json.dumps(events), (
        "the typed prefix was recorded as the recipient value"
    )

    # The demonstration sent one message; clear it so the run's three are the only ones counted.
    harness.corpus_reset()

    outcome = harness.compile_via_popup("Gmail-style compose")
    assert outcome["recipe"] is not None, (
        "the Make recipe form could not compile the recording: "
        f"result={outcome['result']!r} warnings={outcome['warnings']!r} events={events}"
    )
    recipe = outcome["recipe"]
    if os.environ.get("PROCESSLENS_E2E_DUMP_RECIPE") == "1":
        print("COMPILED RECIPE:", json.dumps(recipe, indent=2, sort_keys=True))

    # Requirement 3: the typed prefix and the accept fold into one fill of the chip's value.
    fills = [step for step in recipe["steps"] if step.get("action") == "fill"]
    presses = [step for step in recipe["steps"] if step.get("action") == "press"]
    assert not presses, f"an autocomplete accept must fold away, not stay a press: {presses}"
    assert len(fills) >= 2, f"expected a fill for the recipient and the body: {fills}"

    sample = dict(outcome["row"] or {})
    assert sample, "the popup stored no sample row"
    rows = [_row_from_sample(sample, row["to"], row["message_body"]) for row in golden_rows()]
    run_id = harness.make_run(rows, recipe=recipe)
    records = harness.settle_run(run_id)
    detail = "; ".join(
        f"{record.get('record_key')}={record['state']}:{record.get('reason')}" for record in records
    )
    assert all(record["state"] == "verified" for record in records), detail

    state = harness.corpus_state()
    messages = state.get("messages") or []
    assert state.get("sends") == 3, f"expected exactly three sends: {state} ({detail})"
    assert len(messages) == 3, f"expected exactly three stored messages: {state} ({detail})"

    addresses = [row["to"] for row in golden_rows()]
    for row in golden_rows():
        matching = [message for message in messages if message.get("to") == [row["to"]]]
        assert len(matching) == 1, (
            f"expected exactly one message to {row['to']!r}: {messages} ({detail})"
        )
        assert matching[0].get("body") == row["message_body"], (
            f"message to {row['to']!r} carried the wrong body: {matching[0]!r} ({detail})"
        )
    # No address outside the rows, and none of them twice.
    for message in messages:
        assert message.get("to") in ([address] for address in addresses), (
            f"a message went to a stranger: {message!r} ({detail})"
        )
