"""Compile a recording in the exact shape the extension recorder emits.

The events here reproduce what the extension actually wrote when driven in a
browser: every rung the recorder produces, including the `text_fuzzy` rung that
echoes a text field's own value, and the event envelope with its timestamps. The
values are synthetic; the structure is the recorder's.

This is the "compile a real recording" case: if the recorder's output shape
changes, these tests fail.
"""

from __future__ import annotations

from typing import Any

import pytest

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.validation import validation_errors

URL = "https://crm.example.com/leads/new"

EMAIL_RUNGS: list[dict[str, Any]] = [
    {"by": "role", "role": "textbox", "name": "Email", "within": "form:New lead"},
    {"by": "testid", "value": "lead-email"},
    {"by": "label", "text": "Email"},
    {"by": "css", "selector": "#email"},
    {"by": "text_fuzzy", "value": "ravi@example.com"},
]
INTEREST_RUNGS: list[dict[str, Any]] = [
    {"by": "role", "role": "combobox", "name": "Interest", "within": "form:New lead"},
    {"by": "testid", "value": "lead-interest"},
    {"by": "label", "text": "Interest"},
    {"by": "css", "selector": "#interest"},
    {"by": "text_fuzzy", "value": "Data course"},
]
SAVE_RUNGS: list[dict[str, Any]] = [
    {"by": "role", "role": "button", "name": "Save", "within": "form:New lead"},
    {"by": "css", "selector": "#save"},
    {"by": "text_fuzzy", "value": "Save"},
]


def recorder_event(seq: int, kind: str, step: dict[str, Any]) -> dict[str, Any]:
    """One event in the recorder's envelope, with its timestamp."""
    return {
        "seq": seq,
        "at": f"2026-10-08T00:00:0{seq}.000Z",
        "kind": kind,
        "url": URL,
        "step": step,
    }


@pytest.fixture
def recorded() -> list[dict[str, Any]]:
    """Exactly what the extension wrote for a fill, a select, and a submit."""
    return [
        recorder_event(
            1,
            "fill",
            {
                "id": "fill-1",
                "action": "fill",
                "name": "Fill Email",
                "targets": EMAIL_RUNGS,
                "value": {"kind": "constant", "value": "ravi@example.com"},
                "clear_first": True,
            },
        ),
        recorder_event(
            2,
            "select",
            {
                "id": "select-2",
                "action": "select",
                "name": "Select Interest",
                "targets": INTEREST_RUNGS,
                "value": {"kind": "constant", "value": "Data course"},
                "option_match": "text",
            },
        ),
        recorder_event(
            3,
            "click",
            {"id": "click-3", "action": "click", "name": "Click Save", "targets": SAVE_RUNGS},
        ),
        recorder_event(
            4,
            "submit",
            {"id": "click-4", "action": "click", "name": "Click Save", "targets": SAVE_RUNGS},
        ),
    ]


@pytest.fixture
def options() -> CompileOptions:
    return CompileOptions(
        name="Create a lead from an inquiry sheet",
        when_to_use="Each inquiry has to exist in the CRM exactly once.",
        sample_row={"email": "ravi@example.com", "interest": "Data course"},
        input_fields={
            "email": {"type": "string", "required": True},
            "interest": {"type": "string"},
        },
        record_key_column="email",
        commit_assertions={"click-3": {"text_visible": "Lead created", "timeout_ms": 8000}},
    )


def test_the_recorders_events_fold_into_clean_sequential_steps(
    recorded: list[dict[str, Any]], options: CompileOptions
) -> None:
    steps = compile_recording(recorded, options).recipe["steps"]
    assert [step["action"] for step in steps] == ["navigate", "fill", "select", "click"]


def test_four_recorded_events_become_four_ordered_steps(
    recorded: list[dict[str, Any]], options: CompileOptions
) -> None:
    """The click and the submit it caused are one action, not two."""
    steps = compile_recording(recorded, options).recipe["steps"]
    clicks = [step for step in steps if step["action"] == "click"]
    assert len(clicks) == 1
    assert clicks[0]["commit"] is True


def test_the_compiled_recipe_is_schema_valid(
    recorded: list[dict[str, Any]], options: CompileOptions
) -> None:
    result = compile_recording(recorded, options)
    assert validation_errors(result.recipe) == []


def test_the_typed_values_become_input_columns(
    recorded: list[dict[str, Any]], options: CompileOptions
) -> None:
    steps = compile_recording(recorded, options).recipe["steps"]
    fill = next(step for step in steps if step["action"] == "fill")
    select = next(step for step in steps if step["action"] == "select")
    assert fill["value"] == {"kind": "input", "column": "email"}
    assert select["value"] == {"kind": "input", "column": "interest"}


def test_the_whole_target_ladder_survives_compilation(
    recorded: list[dict[str, Any]], options: CompileOptions
) -> None:
    """Every rung the recorder captured is carried through, in order."""
    steps = compile_recording(recorded, options).recipe["steps"]
    fill = next(step for step in steps if step["action"] == "fill")
    assert [rung["by"] for rung in fill["targets"]] == [
        "role",
        "testid",
        "label",
        "css",
        "text_fuzzy",
    ]
    assert fill["targets"][0]["within"] == "form:New lead"


def test_the_value_echoing_rung_is_not_turned_into_a_template(
    recorded: list[dict[str, Any]], options: CompileOptions
) -> None:
    steps = compile_recording(recorded, options).recipe["steps"]
    fill = next(step for step in steps if step["action"] == "fill")
    fuzzy = next(rung for rung in fill["targets"] if rung["by"] == "text_fuzzy")
    assert fuzzy["value"] == "ravi@example.com"


def test_the_click_is_parameterized_because_its_name_is_a_label(
    recorded: list[dict[str, Any]], options: CompileOptions
) -> None:
    steps = compile_recording(recorded, options).recipe["steps"]
    click = next(step for step in steps if step["action"] == "click")
    assert click["targets"][0]["name"] == "Save"
    assert click["assert_after"]["text_visible"] == "Lead created"


def test_the_origin_is_derived_from_the_recorded_url(
    recorded: list[dict[str, Any]], options: CompileOptions
) -> None:
    meta = compile_recording(recorded, options).recipe["meta"]
    assert meta["allowed_origins"] == ["https://crm.example.com"]


def test_compiling_the_same_recording_twice_gives_the_same_recipe(
    recorded: list[dict[str, Any]], options: CompileOptions
) -> None:
    first = compile_recording(recorded, options).recipe
    second = compile_recording(recorded, options).recipe
    assert first == second
