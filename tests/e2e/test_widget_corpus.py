"""PL-018 requirement 7: each widget type in the corpus records and replays one value.

One case per widget, against the synthetic widget-corpus fixture. Each case records a short
demonstration through the real popup, compiles it, runs one row, and asserts the value the fixture
persisted is exactly the row's — the fixture's own state, not a screenshot.

The report (`internal/agent/reports/PL-018.md`) carries the coverage table, "N of M widget types",
built from these cases. A widget that fails is reported with its cause, never hidden.
"""

from __future__ import annotations

import json
import os

import pytest

from .conftest import simple_recipe

pytestmark = pytest.mark.e2e

NAME_COLUMN_HINTS = ("name", "editor", "body", "text", "message", "email", "assignee", "framed")
PLAN_COLUMN_HINTS = ("plan", "select")


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
    harness.start_worker(worker_id="corpus-warm-up", max_count=1, max_seconds=90)
    try:
        harness.wait_for_terminal(run_id, timeout=90.0)
    finally:
        harness.stop_workers()


def _row_from_sample(
    sample: dict[str, str], text_value: str, plan_value: str | None = None
) -> dict[str, str]:
    """One run row from the compiled sample row, by column-name hints."""
    row: dict[str, str] = {}
    for column, value in sample.items():
        lowered = column.lower()
        if plan_value is not None and any(hint in lowered for hint in PLAN_COLUMN_HINTS):
            row[column] = plan_value
        elif any(hint in lowered for hint in NAME_COLUMN_HINTS) or "@" in str(value):
            row[column] = text_value
        else:
            row[column] = value
    return row


def record_compile_run(
    harness,
    *,
    name: str,
    page_path: str,
    interact,
    text_value: str,
    plan_value: str | None = None,
) -> tuple[dict, dict, list[dict]]:
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
    outcome = harness.compile_via_popup(name)
    assert outcome["recipe"] is not None, (
        f"{name}: the Make recipe form could not compile the recording: "
        f"result={outcome['result']!r} warnings={outcome['warnings']!r} events={events}"
    )
    recipe = outcome["recipe"]
    sample = dict(outcome["row"] or {})
    assert sample, f"{name}: the popup stored no sample row"

    rows = [_row_from_sample(sample, text_value, plan_value)]
    run_id = harness.make_run(rows, recipe=recipe)
    records = harness.settle_run(run_id)
    detail = "; ".join(
        f"{record.get('record_key')}={record['state']}:{record.get('reason')}" for record in records
    )
    assert all(record["state"] == "verified" for record in records), f"{name}: {detail}"
    return recipe, outcome, harness.corpus_state()


def assert_state_value(state: dict, key: str, expected: str, *, name: str) -> None:
    """The fixture's own truth for one widget, with a message naming what it actually holds."""
    assert key in state, f"{name}: the fixture reports no {key!r}; it reports {sorted(state)}"
    assert state[key] == expected, (
        f"{name}: the fixture holds {state[key]!r} for {key!r}, expected {expected!r}"
    )


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
        page.wait_for_selector('[data-testid="saved"]', timeout=10_000)

    _recipe, _outcome, state = record_compile_run(
        harness,
        name="Plain editor",
        page_path="/plain-editor.html",
        interact=interact,
        text_value=text,
    )
    assert_state_value(state, "editor_text", text, name="plain editor")


def test_widget_framework_editor(corpus_harness) -> None:
    """A contenteditable mutated by script with no `input` event: only the state diff can see it."""
    harness = corpus_harness
    warm_up_corpus(harness)
    text = "Framework editor line"

    def interact(page) -> None:
        page.click('[data-testid="editor"]')
        page.keyboard.type(text)
        page.click('[data-testid="save"]')
        page.wait_for_selector('[data-testid="saved"]', timeout=10_000)

    _recipe, _outcome, state = record_compile_run(
        harness,
        name="Framework editor",
        page_path="/framework-editor.html",
        interact=interact,
        text_value=text,
    )
    assert_state_value(state, "editor_text", text, name="framework editor")


def test_widget_react_autocomplete(corpus_harness) -> None:
    """An autocomplete accepted by clicking the option: the click must follow the typing."""
    harness = corpus_harness
    warm_up_corpus(harness)
    address = "grace@example.test"

    def interact(page) -> None:
        page.click('[data-testid="assignee"]')
        page.keyboard.type("grace")
        page.wait_for_selector('[data-testid="options"] [role="option"]', timeout=5_000)
        page.click(f'[data-testid="options"] [role="option"]:has-text("{address}")')
        page.click('[data-testid="save"]')
        page.wait_for_selector('[data-testid="saved"]', timeout=10_000)

    _recipe, _outcome, state = record_compile_run(
        harness,
        name="React autocomplete",
        page_path="/react-autocomplete.html",
        interact=interact,
        text_value=address,
    )
    assert_state_value(state, "assignee", address, name="react autocomplete")


def test_widget_shadow_form(corpus_harness) -> None:
    """A form inside an open shadow root: both fields must be recorded and replayed."""
    harness = corpus_harness
    warm_up_corpus(harness)
    text = "Shadow subject"
    plan = "Pro"

    def interact(page) -> None:
        page.evaluate(
            "() => { const root = document.querySelector('[data-testid=\"shadow-host\"]')"
            ".shadowRoot;"
            " const name = root.querySelector('[aria-label=\"Shadow name\"]');"
            " name.focus(); }"
        )
        page.keyboard.type(text)
        page.evaluate(
            "() => { const root = document.querySelector('[data-testid=\"shadow-host\"]')"
            ".shadowRoot;"
            " const select = root.querySelector('[aria-label=\"Shadow plan\"]');"
            " select.value = 'pro';"
            " select.dispatchEvent(new Event('change', { bubbles: true })); }"
        )
        page.evaluate(
            "() => document.querySelector('[data-testid=\"shadow-host\"]')"
            ".shadowRoot.querySelector('[data-testid=\"shadow-save\"]').click()"
        )
        page.wait_for_selector('[data-testid="saved"]', timeout=10_000)

    _recipe, _outcome, state = record_compile_run(
        harness,
        name="Shadow form",
        page_path="/shadow-form.html",
        interact=interact,
        text_value=text,
        plan_value=plan,
    )
    assert_state_value(state, "shadow_name", text, name="shadow form")
    assert_state_value(state, "shadow_plan", plan, name="shadow form")


def test_widget_iframe_form(corpus_harness) -> None:
    """A form inside a same-origin iframe: the frame path must survive the round trip."""
    harness = corpus_harness
    warm_up_corpus(harness)
    text = "framed@example.test"

    def interact(page) -> None:
        frame = page.frame_locator('[data-testid="form-frame"]')
        frame.locator('[aria-label="Framed email"]').click()
        page.keyboard.type(text)
        frame.locator('[data-testid="framed-save"]').click()
        page.wait_for_selector('[data-testid="saved"]', timeout=10_000)

    _recipe, _outcome, state = record_compile_run(
        harness,
        name="Iframe form",
        page_path="/iframe-form.html",
        interact=interact,
        text_value=text,
    )
    assert_state_value(state, "framed_email", text, name="iframe form")
