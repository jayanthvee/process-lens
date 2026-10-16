"""PL-017 built-in rules: the duplicate-search branch and the required-field park rules.

These are the deterministic layer of the recipe-building approach (D-019): the compiler's own rules
come first, with no model call. The ticket's tests live here.
"""

from __future__ import annotations

from typing import Any

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.testing import EMAIL_TARGETS, SAVE_TARGETS, action_event
from packages.compiler.validation import validation_errors

SEARCH_BOX = [{"by": "role", "role": "searchbox", "name": "Search leads"}]
SEARCH_BUTTON = [{"by": "role", "role": "button", "name": "Search"}]
NEW_LEAD_LINK = [{"by": "role", "role": "link", "name": "New lead"}]

SAMPLE_ROW = {"email": "ravi@example.com", "interest": "Data course", "company": "Ravi Co"}


def _options(**overrides: Any) -> CompileOptions:
    base: dict[str, Any] = {
        "name": "New lead",
        "when_to_use": "New lead",
        "sample_row": dict(SAMPLE_ROW),
        "record_key_column": "email",
    }
    base.update(overrides)
    return CompileOptions(**base)


def _search_then_create() -> list[dict[str, Any]]:
    """Search for the key, then New lead -> fill -> Save: the flow PL-017 requirement 6 names."""
    return [
        action_event(1, "fill", "fill", SEARCH_BOX, value="ravi@example.com"),
        action_event(2, "click", "click", SEARCH_BUTTON),
        action_event(3, "click", "click", NEW_LEAD_LINK),
        action_event(4, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        action_event(5, "click", "click", SAVE_TARGETS, step_id="save-1"),
        action_event(
            6,
            "submit",
            "click",
            SAVE_TARGETS,
            step_id="save-1",
            observation={"text_visible": "Created"},
        ),
    ]


def _branch(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((step for step in steps if step.get("action") == "branch"), None)


def test_a_search_for_the_record_key_produces_the_duplicate_branch() -> None:
    options = _options(commit_step_ids=["save-1"])
    result = compile_recording(_search_then_create(), options)

    steps = result.recipe["steps"]
    actions = [step["action"] for step in steps]
    assert actions == ["navigate", "fill", "click", "branch", "click", "fill", "click"], actions

    branch = _branch(steps)
    assert branch is not None
    # PL-027: the condition is the destination's own results table, not page-wide text. On Odoo the
    # search view renders the query back as a facet, so "this text is visible" is true for a record
    # that does not exist and the branch would skip every row of the batch.
    assert branch["when"] == {
        "element_present": {
            "targets": [{"by": "role", "role": "cell", "name": "{{row.email | lower}}"}]
        }
    }
    assert branch["then"] == [
        {"id": "end-record-1", "action": "end_record", "outcome": "skipped", "reason": "duplicate"}
    ]
    # The search itself reads the destination; it is not the record's commit.
    search_click = steps[2]
    assert "commit" not in search_click
    assert steps[-1].get("commit") is True
    assert validation_errors(result.recipe) == []


def test_the_duplicate_branch_can_be_turned_off() -> None:
    options = _options(commit_step_ids=["save-1"], duplicate_check=False)
    steps = compile_recording(_search_then_create(), options).recipe["steps"]
    assert _branch(steps) is None


def test_a_recording_with_no_search_gets_no_branch() -> None:
    """Fill the key, choose, save -- the create flow has no search, so no branch."""
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        action_event(2, "select", "select", SEARCH_BOX, value="Data course"),
        action_event(3, "click", "click", SAVE_TARGETS, step_id="save-1"),
        action_event(
            4,
            "submit",
            "click",
            SAVE_TARGETS,
            step_id="save-1",
            observation={"text_visible": "Created"},
        ),
    ]
    steps = compile_recording(recording, _options(commit_step_ids=["save-1"])).recipe["steps"]
    assert _branch(steps) is None


def test_a_wizard_fill_then_next_is_not_a_search() -> None:
    """A fill of the key immediately followed by a click that is not a search must not branch."""
    next_button = [{"by": "role", "role": "button", "name": "Next"}]
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        action_event(2, "click", "click", next_button),
        action_event(3, "click", "click", SAVE_TARGETS, step_id="save-1"),
        action_event(
            4,
            "submit",
            "click",
            SAVE_TARGETS,
            step_id="save-1",
            observation={"text_visible": "Created"},
        ),
    ]
    steps = compile_recording(recording, _options(commit_step_ids=["save-1"])).recipe["steps"]
    assert _branch(steps) is None


def test_ticked_fields_become_park_rules() -> None:
    options = _options(commit_step_ids=["save-1"], park_fields=["company", "email"])
    input_schema = compile_recording(_search_then_create(), options).recipe["meta"]["input_schema"]
    assert input_schema["rules"] == [
        {"when": {"missing": "company"}, "then": "park", "reason": "missing company"},
        {"when": {"missing": "email"}, "then": "park", "reason": "missing email"},
    ]
    assert validation_errors(compile_recording(_search_then_create(), options).recipe) == []


def test_no_ticked_fields_means_no_rules() -> None:
    input_schema = compile_recording(
        _search_then_create(), _options(commit_step_ids=["save-1"])
    ).recipe["meta"]["input_schema"]
    assert "rules" not in input_schema
