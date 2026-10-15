"""PL-018 requirement 7: each widget type in the corpus records and replays one value.

One case per widget, against the synthetic widget-corpus fixture. Each case records a short
demonstration through the real popup, compiles it, runs one row whose value differs from the
demonstrated one, and asserts the value the fixture persisted is exactly the row's — the fixture's
own state (`/__test__/state`), not a screenshot.

The report (`internal/agent/reports/PL-018.md`) carries the coverage table, "N of M widget types",
built from these cases. A widget that fails is reported with its cause, never hidden.
"""

from __future__ import annotations

import json
import os

import pytest

from .conftest import simple_recipe

pytestmark = pytest.mark.e2e

# Column-name hints: the popup derives columns from the recorded fields' accessible names, so the
# test maps the row's value onto whichever column carries the widget's editable field.
TEXT_COLUMN_HINTS = ("note", "draft", "editor", "body", "text", "message", "reviewer", "assignee")
SELECT_COLUMN_HINTS = ("team", "plan", "select", "status")


def warm_up_corpus(harness) -> None:
    """The corpus tab's own cold-command warm-up; see the golden file for the reason."""
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


def _row_from_sample(
    sample: dict[str, str], text_value: str, select_value: str | None = None
) -> dict[str, str]:
    """One run row from the compiled sample row, by column-name hints.

    The widget's editable column gets the row's value; a select-shaped column gets `select_value`
    when one is given. Every other recorded column keeps the demonstrated value.
    """
    row: dict[str, str] = {}
    for column, value in sample.items():
        lowered = column.lower()
        if select_value is not None and any(hint in lowered for hint in SELECT_COLUMN_HINTS):
            row[column] = select_value
        elif any(hint in lowered for hint in TEXT_COLUMN_HINTS) or "@" in str(value):
            row[column] = text_value
        else:
            row[column] = value
    return row


def choose_record_key(harness) -> None:
    """Pick the form's first record-key column when it defaulted to none.

    The popup disables Make recipe until a key is chosen, and it only auto-chooses one when a
    recorded value looks like an email. These widgets persist free text, so the person would pick
    the first column; the test does the same.
    """
    harness.popup.evaluate(
        "() => { const sel = document.getElementById('make-key');"
        " if (sel && !sel.value && sel.options.length > 0) {"
        " sel.value = sel.options[0].value;"
        " sel.dispatchEvent(new Event('change', { bubbles: true })); } }"
    )


def record_compile_run(
    harness,
    *,
    name: str,
    page_path: str,
    interact,
    text_value: str,
    select_value: str | None = None,
) -> dict:
    """Record one demonstration, compile it through the popup, run one row, return the state."""
    harness.corpus_reset()
    harness.page.goto(harness.crm_url + page_path)
    harness.start_recording()
    interact(harness.page)
    harness.stop_recording()

    events = harness.recording_events()
    assert events, f"{name}: the recorder captured nothing"
    if os.environ.get("PROCESSLENS_E2E_DUMP_RECIPE") == "1":
        print(f"{name} EVENTS:", json.dumps(events, indent=2, sort_keys=True))

    harness.corpus_reset()
    choose_record_key(harness)
    outcome = harness.compile_via_popup(name)
    assert outcome["recipe"] is not None, (
        f"{name}: the Make recipe form could not compile the recording: "
        f"result={outcome['result']!r} warnings={outcome['warnings']!r} events={events}"
    )
    sample = dict(outcome["row"] or {})
    assert sample, f"{name}: the popup stored no sample row"

    rows = [_row_from_sample(sample, text_value, select_value)]
    run_id = harness.make_run(rows, recipe=outcome["recipe"])
    records = harness.settle_run(run_id)
    detail = "; ".join(
        f"{record.get('record_key')}={record['state']}:{record.get('reason')}" for record in records
    )
    assert all(record["state"] == "verified" for record in records), f"{name}: {detail}"
    return harness.corpus_state()


def widget_values(state: dict, source: str) -> dict:
    """The values one widget persisted, keyed by the field the fixture stores."""
    values = state.get("values") or {}
    assert source in values, (
        f"the fixture reports no saved values for {source!r}; it reports {sorted(values)}"
    )
    return values[source]


# ---------------------------------------------------------------- the widgets


def test_widget_plain_editor(corpus_harness) -> None:
    """A contenteditable that fires `input` normally: the baseline that must not regress."""
    harness = corpus_harness
    warm_up_corpus(harness)
    text = "Plain editor line"

    def interact(page) -> None:
        page.click('[data-testid="editor"]')
        page.keyboard.type(text)
        page.click('[data-testid="save"]')
        page.wait_for_selector('[data-testid="saved"]:not([hidden])', timeout=10_000)

    state = record_compile_run(
        harness,
        name="Plain editor",
        page_path="/plain-editor.html",
        interact=interact,
        text_value=text,
    )
    assert widget_values(state, "plain-editor")["note"] == text


def test_widget_framework_editor(corpus_harness) -> None:
    """A contenteditable mutated by script with no `input` event: only the state diff can see it."""
    harness = corpus_harness
    warm_up_corpus(harness)
    text = "Framework editor line"

    def interact(page) -> None:
        page.click('[data-testid="editor"]')
        page.keyboard.type(text)
        page.click('[data-testid="save"]')
        page.wait_for_selector('[data-testid="saved"]:not([hidden])', timeout=10_000)

    state = record_compile_run(
        harness,
        name="Framework editor",
        page_path="/framework-editor.html",
        interact=interact,
        text_value=text,
    )
    assert widget_values(state, "framework-editor")["draft"] == text


def test_widget_react_autocomplete(corpus_harness) -> None:
    """An autocomplete accepted by clicking the option: the click must follow the typing."""
    harness = corpus_harness
    warm_up_corpus(harness)
    address = "carol@example.test"

    def interact(page) -> None:
        page.click('[data-testid="reviewer"]')
        page.keyboard.type("carol")
        page.wait_for_selector('#reviewer-suggestions [role="option"]', timeout=5_000)
        page.click(f'#reviewer-suggestions [data-address="{address}"]')
        page.click('[data-testid="save"]')
        page.wait_for_selector('[data-testid="saved"]:not([hidden])', timeout=10_000)

    state = record_compile_run(
        harness,
        name="React autocomplete",
        page_path="/react-autocomplete.html",
        interact=interact,
        text_value=address,
    )
    assert widget_values(state, "react-autocomplete")["reviewer"] == address


def test_widget_shadow_form(corpus_harness) -> None:
    """A form inside an open shadow root: both fields must be recorded and replayed."""
    harness = corpus_harness
    warm_up_corpus(harness)
    text = "Shadow subject"
    team = "Support"

    def interact(page) -> None:
        page.click('[data-testid="shadow-name"]')
        page.keyboard.type(text)
        page.select_option('[data-testid="shadow-team"]', value="support")
        page.click('[data-testid="shadow-save"]')
        page.wait_for_selector('[data-testid="shadow-saved"]:not([hidden])', timeout=10_000)

    state = record_compile_run(
        harness,
        name="Shadow form",
        page_path="/shadow-form.html",
        interact=interact,
        text_value=text,
        select_value=team,
    )
    values = widget_values(state, "shadow-form")
    assert values["name"] == text
    # The page stores the select's own value ("support"), never the option's
    # label ("Support"): the row asked for the label so the replay exercised
    # text matching, and the fixture's truth is the value it persisted.
    assert values["team"] == "support"


def test_widget_iframe_form(corpus_harness) -> None:
    """A form inside a same-origin iframe: the frame path must survive the round trip."""
    harness = corpus_harness
    warm_up_corpus(harness)
    text = "Framed note text"

    def interact(page) -> None:
        frame = page.frame_locator('[data-testid="frame"]')
        frame.locator('[data-testid="frame-note"]').click()
        page.keyboard.type(text)
        frame.locator('[data-testid="frame-save"]').click()
        # The framed Save answers in its own document; the parent's status is a manual button.
        frame.locator('[data-testid="frame-saved"]:not([hidden])').wait_for(timeout=10_000)

    state = record_compile_run(
        harness,
        name="Iframe form",
        page_path="/iframe-form.html",
        interact=interact,
        text_value=text,
    )
    assert widget_values(state, "iframe-form")["note"] == text
