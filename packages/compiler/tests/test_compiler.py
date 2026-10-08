"""End-to-end compilation: a recording in, a schema-valid recipe out."""

from __future__ import annotations

from typing import Any

import pytest

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.errors import CompileError
from packages.compiler.testing import (
    EMAIL_TARGETS,
    INPUT_FIELDS,
    INTEREST_TARGETS,
    SAMPLE_ROW,
    SAVE_TARGETS,
    action_event,
    noise_event,
)
from packages.compiler.validation import validation_errors

# --- the compiled recipe is valid and well formed --------------------------


def test_the_compiled_recipe_validates_against_the_schema(
    recording: list[dict[str, Any]], options: CompileOptions
) -> None:
    result = compile_recording(recording, options)
    assert validation_errors(result.recipe) == []


def test_the_processed_recording_folds_into_clean_sequential_steps(
    recording: list[dict[str, Any]], options: CompileOptions
) -> None:
    result = compile_recording(recording, options)
    steps = result.recipe["steps"]
    assert [step["action"] for step in steps] == ["navigate", "fill", "select", "click"]


def test_step_ids_are_unique_and_well_formed(
    recording: list[dict[str, Any]], options: CompileOptions
) -> None:
    steps = compile_recording(recording, options).recipe["steps"]
    ids = [step["id"] for step in steps]
    assert len(set(ids)) == len(ids)
    assert all(step_id == step_id.lower() and " " not in step_id for step_id in ids)


def test_the_meta_block_is_complete(
    recording: list[dict[str, Any]], options: CompileOptions
) -> None:
    meta = compile_recording(recording, options).recipe["meta"]
    assert meta["name"] == "Create a record from a sheet"
    assert set(meta["input_schema"]["fields"]) == {"email", "interest"}
    assert meta["record_key"] == "{{row.email | lower}}"
    assert meta["idempotency_key"] == "{{row.email | lower}}"
    assert meta["approval_policy"] == {"commit": "first_record"}
    assert meta["allowed_origins"] == ["https://app.example.com"]


def test_the_start_url_becomes_a_navigate_step(
    recording: list[dict[str, Any]], options: CompileOptions
) -> None:
    steps = compile_recording(recording, options).recipe["steps"]
    assert steps[0]["action"] == "navigate"
    assert steps[0]["url"] == "https://app.example.com/records/new"


def test_navigation_can_be_left_out(
    recording: list[dict[str, Any]], options: CompileOptions
) -> None:
    options.include_navigate = False
    steps = compile_recording(recording, options).recipe["steps"]
    assert all(step["action"] != "navigate" for step in steps)


def test_the_compiler_is_deterministic(
    recording: list[dict[str, Any]], options: CompileOptions
) -> None:
    first = compile_recording(recording, options).recipe
    second = compile_recording(recording, options).recipe
    assert first == second


def test_an_empty_recording_is_rejected() -> None:
    with pytest.raises(CompileError, match="no click, fill, select, or submit"):
        compile_recording([noise_event(1, "focus")], CompileOptions(name="n", when_to_use="w"))


# --- parameterization ------------------------------------------------------


def test_recorded_literals_become_references_to_the_input_row(
    recording: list[dict[str, Any]], options: CompileOptions
) -> None:
    steps = compile_recording(recording, options).recipe["steps"]
    fill = next(step for step in steps if step["action"] == "fill")
    select = next(step for step in steps if step["action"] == "select")
    assert fill["value"] == {"kind": "input", "column": "email"}
    assert select["value"] == {"kind": "input", "column": "interest"}


def test_a_literal_with_no_column_stays_a_constant(options: CompileOptions) -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="nobody@example.com"),
        action_event(2, "click", "click", SAVE_TARGETS),
        action_event(3, "submit", "click", SAVE_TARGETS, observation={"text_visible": "Saved"}),
    ]
    steps = compile_recording(recording, options).recipe["steps"]
    fill = next(step for step in steps if step["action"] == "fill")
    assert fill["value"] == {"kind": "constant", "value": "nobody@example.com"}


def test_a_recorded_literal_in_a_target_rung_becomes_a_template(options: CompileOptions) -> None:
    """A search-result cell carries the record's own value, so it is templated."""
    result_cell = [{"by": "role", "role": "cell", "name": "ravi@example.com"}]
    recording = [
        action_event(1, "click", "click", result_cell),
        action_event(2, "click", "click", SAVE_TARGETS),
        action_event(3, "submit", "click", SAVE_TARGETS, observation={"text_visible": "Saved"}),
    ]
    steps = compile_recording(recording, options).recipe["steps"]
    click = next(
        step for step in steps if step["action"] == "click" and step["targets"][0]["name"] != "Save"
    )
    assert click["targets"][0]["name"] == "{{row.email}}"


# --- commit steps and assertions -------------------------------------------


def test_the_click_submit_pair_becomes_one_commit_step(
    recording: list[dict[str, Any]], options: CompileOptions
) -> None:
    steps = compile_recording(recording, options).recipe["steps"]
    commits = [step for step in steps if step.get("commit") is True]
    assert len(commits) == 1
    assert commits[0]["action"] == "click"


def test_the_commit_step_receives_the_observed_assertion(
    recording: list[dict[str, Any]], options: CompileOptions
) -> None:
    steps = compile_recording(recording, options).recipe["steps"]
    commit = next(step for step in steps if step.get("commit") is True)
    assert commit["assert_after"] == {"text_visible": "Record created", "timeout_ms": 8000}


def test_a_commit_without_a_click_uses_a_navigation_assertion(options: CompileOptions) -> None:
    """Submitting with Enter navigates to the record list; that is the evidence."""
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        action_event(2, "submit", "click", SAVE_TARGETS),
        action_event(
            3,
            "click",
            "click",
            INTEREST_TARGETS,
            url="https://app.example.com/records",
        ),
    ]
    steps = compile_recording(recording, options).recipe["steps"]
    commit = next(step for step in steps if step.get("commit") is True)
    assert commit["assert_after"]["url_matches"] == "/records"
    assert "Record created" not in str(commit["assert_after"])


def test_a_commit_with_nothing_to_assert_is_refused(options: CompileOptions) -> None:
    recording = [
        action_event(1, "click", "click", SAVE_TARGETS),
        action_event(2, "submit", "click", SAVE_TARGETS),
    ]
    with pytest.raises(CompileError, match="must carry an assertion"):
        compile_recording(recording, options)


def test_a_supplied_assertion_is_used_when_the_recording_observed_nothing(
    options: CompileOptions,
) -> None:
    """A recorder that captures no observation still compiles when the builder decides."""
    options.commit_assertions = {"click-1": {"text_visible": "Lead created", "timeout_ms": 5000}}
    recording = [
        action_event(1, "click", "click", SAVE_TARGETS, step_id="click-1"),
        action_event(2, "submit", "click", SAVE_TARGETS, step_id="click-1"),
    ]
    result = compile_recording(recording, options)
    commit = next(step for step in result.recipe["steps"] if step.get("commit") is True)
    assert commit["assert_after"] == {"text_visible": "Lead created", "timeout_ms": 5000}
    assert validation_errors(result.recipe) == []
    assert any("supplied for it" in warning for warning in result.warnings)


def test_a_supplied_assertion_outranks_a_recorded_observation(options: CompileOptions) -> None:
    options.commit_assertions = {"click-1": {"url_matches": "/leads/"}}
    recording = [
        action_event(1, "click", "click", SAVE_TARGETS, step_id="click-1"),
        action_event(
            2,
            "submit",
            "click",
            SAVE_TARGETS,
            step_id="click-1",
            observation={"text_visible": "Saved"},
        ),
    ]
    result = compile_recording(recording, options)
    commit = next(step for step in result.recipe["steps"] if step.get("commit") is True)
    assert commit["assert_after"] == {"url_matches": "/leads/"}


def test_a_step_marked_commit_by_the_caller_needs_an_assertion(options: CompileOptions) -> None:
    recording = [action_event(1, "select", "select", INTEREST_TARGETS, value="Data course")]
    options.commit_step_ids = ["select-1"]
    with pytest.raises(CompileError, match="must carry an assertion"):
        compile_recording(recording, options)


def test_a_caller_marked_commit_is_honoured_with_an_observation(options: CompileOptions) -> None:
    recording = [
        action_event(
            1,
            "select",
            "select",
            INTEREST_TARGETS,
            value="Data course",
            observation={"text_visible": "Price updated"},
        )
    ]
    options.commit_step_ids = ["select-1"]
    steps = compile_recording(recording, options).recipe["steps"]
    commit = next(step for step in steps if step.get("commit") is True)
    assert commit["assert_after"]["text_visible"] == "Price updated"


def test_normal_steps_are_not_commits(
    recording: list[dict[str, Any]], options: CompileOptions
) -> None:
    steps = compile_recording(recording, options).recipe["steps"]
    for step in steps:
        if step["action"] in ("navigate", "fill", "select"):
            assert "commit" not in step
            assert "assert_after" not in step


# --- noise, frames, warnings ----------------------------------------------


def test_focus_and_scroll_noise_never_reaches_the_recipe(options: CompileOptions) -> None:
    recording = [
        noise_event(1, "focus"),
        noise_event(2, "blur"),
        noise_event(3, "scroll"),
        action_event(4, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        noise_event(5, "mousemove"),
        action_event(6, "click", "click", SAVE_TARGETS),
        action_event(7, "submit", "click", SAVE_TARGETS, observation={"text_visible": "Saved"}),
    ]
    result = compile_recording(recording, options)
    assert [step["action"] for step in result.recipe["steps"]] == ["navigate", "fill", "click"]
    assert validation_errors(result.recipe) == []


def test_an_iframe_path_is_carried_onto_the_step(options: CompileOptions) -> None:
    frame = [{"by": "index", "index": 0}]
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com", frame=frame),
        action_event(2, "click", "click", SAVE_TARGETS, frame=frame),
        action_event(
            3, "submit", "click", SAVE_TARGETS, frame=frame, observation={"text_visible": "Saved"}
        ),
    ]
    steps = compile_recording(recording, options).recipe["steps"]
    fill = next(step for step in steps if step["action"] == "fill")
    assert fill["frame"] == frame


def test_an_empty_fill_is_dropped_with_a_warning(options: CompileOptions) -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value=""),
        action_event(2, "click", "click", SAVE_TARGETS),
        action_event(3, "submit", "click", SAVE_TARGETS, observation={"text_visible": "Saved"}),
    ]
    result = compile_recording(recording, options)
    assert all(step["action"] != "fill" for step in result.recipe["steps"])
    assert any("empty fill" in warning for warning in result.warnings)


def test_a_step_whose_target_was_not_captured_is_dropped_with_a_warning(
    options: CompileOptions,
) -> None:
    recording = [
        {
            "seq": 1,
            "kind": "click",
            "url": "https://app.example.com/records/new",
            "step": {"action": "click", "targets": []},
        },
        action_event(2, "click", "click", SAVE_TARGETS),
        action_event(3, "submit", "click", SAVE_TARGETS, observation={"text_visible": "Saved"}),
    ]
    result = compile_recording(recording, options)
    assert any("target was not captured" in warning for warning in result.warnings)


def test_warnings_explain_the_folds_that_happened(
    recording: list[dict[str, Any]], options: CompileOptions
) -> None:
    warnings = compile_recording(recording, options).warnings
    assert any("noise" in warning for warning in warnings)
    assert any("merged" in warning for warning in warnings)
    assert any("folded" in warning for warning in warnings)


# --- options and refusals --------------------------------------------------


def test_allowed_origins_can_be_supplied(
    recording: list[dict[str, Any]], options: CompileOptions
) -> None:
    options.allowed_origins = ["https://crm.example.com"]
    meta = compile_recording(recording, options).recipe["meta"]
    assert meta["allowed_origins"] == ["https://crm.example.com"]


def test_origins_are_derived_from_every_page_the_recording_touched(options: CompileOptions) -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        action_event(2, "click", "click", SAVE_TARGETS, url="https://portal.example.net/list"),
    ]
    meta = compile_recording(recording, options).recipe["meta"]
    assert meta["allowed_origins"] == ["https://app.example.com", "https://portal.example.net"]


def test_a_missing_record_key_is_refused(recording: list[dict[str, Any]]) -> None:
    options = CompileOptions(name="n", when_to_use="w", input_fields=dict(INPUT_FIELDS))
    with pytest.raises(CompileError, match="record_key is required"):
        compile_recording(recording, options)


def test_a_missing_input_schema_is_refused(recording: list[dict[str, Any]]) -> None:
    options = CompileOptions(name="n", when_to_use="w", record_key="{{row.email}}")
    with pytest.raises(CompileError, match="input schema is required"):
        compile_recording(recording, options)


def test_the_input_schema_is_derived_from_the_sample_row(recording: list[dict[str, Any]]) -> None:
    options = CompileOptions(
        name="n",
        when_to_use="w",
        sample_row=dict(SAMPLE_ROW),
        record_key_column="email",
    )
    result = compile_recording(recording, options)
    fields = result.recipe["meta"]["input_schema"]["fields"]
    assert set(fields) == {"email", "interest"}
    assert all(field["type"] == "string" for field in fields.values())
    assert any("derived the input schema" in warning for warning in result.warnings)


def test_precheck_rules_are_carried_into_the_input_schema(
    recording: list[dict[str, Any]], options: CompileOptions
) -> None:
    options.input_rules = [
        {"when": {"missing": "email"}, "then": "park", "reason": "No email address."}
    ]
    input_schema = compile_recording(recording, options).recipe["meta"]["input_schema"]
    assert input_schema["rules"] == options.input_rules


def test_a_recording_with_no_urls_cannot_derive_origins(options: CompileOptions) -> None:
    recording = [action_event(1, "click", "click", SAVE_TARGETS, url="")]
    with pytest.raises(CompileError, match="no page URLs"):
        compile_recording(recording, options)


def test_an_input_column_name_is_a_valid_recipe_name(options: CompileOptions) -> None:
    """The derived record key must satisfy the schema's name pattern."""
    result = compile_recording(
        [
            action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
            action_event(2, "click", "click", SAVE_TARGETS),
            action_event(3, "submit", "click", SAVE_TARGETS, observation={"text_visible": "Saved"}),
        ],
        options,
    )
    assert validation_errors(result.recipe) == []
