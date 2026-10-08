"""The compiler consumes the recorder's post-action observation events.

The recorder reports what the page showed after a click or a submit on its own
event of kind ``observation``. The compiler folds each one onto the action it
followed, so a commit step gets an ``assert_after`` without the caller having to
supply one.
"""

from __future__ import annotations

from typing import Any

import pytest

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.errors import CompileError
from packages.compiler.normalize import parse_events
from packages.compiler.testing import (
    EMAIL_TARGETS,
    INPUT_FIELDS,
    INTEREST_TARGETS,
    SAMPLE_ROW,
    SAVE_TARGETS,
    action_event,
)
from packages.compiler.validation import validation_errors


def observation_event(seq: int, observation: dict[str, Any], url: str) -> dict[str, Any]:
    """A recorded observation, in the shape the extension emits as its own event."""
    return {
        "seq": seq,
        "kind": "observation",
        "url": url,
        "step": None,
        "observation": observation,
    }


def options() -> CompileOptions:
    return CompileOptions(
        name="Create a record from a sheet",
        when_to_use="Each row has to exist in the application exactly once.",
        sample_row=dict(SAMPLE_ROW),
        input_fields={name: dict(field) for name, field in INPUT_FIELDS.items()},
        record_key_column="email",
    )


def test_a_confirmation_observation_asserts_the_commit() -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        action_event(2, "select", "select", INTEREST_TARGETS, value="Data course"),
        action_event(3, "click", "click", SAVE_TARGETS, name="Click Save"),
        action_event(4, "submit", "click", SAVE_TARGETS),
        observation_event(
            5, {"text_visible": "Record created"}, "https://app.example.com/records/new"
        ),
    ]

    result = compile_recording(recording, options())

    commit = result.recipe["steps"][-1]
    assert commit["action"] == "click"
    assert commit["commit"] is True
    assert commit["assert_after"] == {"text_visible": "Record created", "timeout_ms": 8000}
    assert validation_errors(result.recipe) == []
    assert any("recorded observation" in warning for warning in result.warnings)


def test_a_url_observation_asserts_the_commit_without_a_manual_override() -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        action_event(2, "click", "click", SAVE_TARGETS, name="Click Save"),
        action_event(3, "submit", "click", SAVE_TARGETS),
        observation_event(4, {"url_matches": "/records/"}, "https://app.example.com/records/42"),
    ]

    result = compile_recording(recording, options())

    commit = result.recipe["steps"][-1]
    assert commit["assert_after"] == {"url_matches": "/records/", "timeout_ms": 8000}
    assert validation_errors(result.recipe) == []
    assert any("attached 1 observation" in warning for warning in result.warnings)


def test_an_observation_follows_the_action_not_a_later_fill() -> None:
    """The observation lands on the Save it followed, not the field typed after it."""
    events, _ = parse_events(
        [
            action_event(1, "click", "click", SAVE_TARGETS, name="Click Save"),
            action_event(2, "submit", "click", SAVE_TARGETS),
            action_event(3, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
            observation_event(
                4, {"text_visible": "Record created"}, "https://app.example.com/records/new"
            ),
        ]
    )

    by_kind = {event.kind: event for event in events}
    assert by_kind["submit"].observation == {"text_visible": "Record created"}
    assert by_kind["fill"].observation is None


def test_an_observation_with_no_action_before_it_is_dropped() -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        observation_event(
            2, {"text_visible": "Record created"}, "https://app.example.com/records/new"
        ),
    ]

    result = compile_recording(recording, options())

    assert [step["action"] for step in result.recipe["steps"]] == ["navigate", "fill"]
    assert all(step.get("commit") is not True for step in result.recipe["steps"])
    assert any("followed no action" in warning for warning in result.warnings)


def test_an_observation_is_not_a_step_of_its_own() -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        action_event(2, "click", "click", SAVE_TARGETS, name="Click Save"),
        action_event(3, "submit", "click", SAVE_TARGETS),
        observation_event(
            4, {"text_visible": "Record created"}, "https://app.example.com/records/new"
        ),
    ]

    result = compile_recording(recording, options())

    assert [step["action"] for step in result.recipe["steps"]] == ["navigate", "fill", "click"]


def test_a_commit_with_no_observation_still_fails_closed() -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        action_event(2, "click", "click", SAVE_TARGETS, name="Click Save"),
        action_event(3, "submit", "click", SAVE_TARGETS),
    ]

    with pytest.raises(CompileError):
        compile_recording(recording, options())
