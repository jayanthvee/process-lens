"""The compiler turns a recorded navigation into a navigate step at its position.

The recorder reports a top-level navigation nobody's click caused -- a typed url, back/forward, a
reload -- as its own ``navigate`` event. The compiler places a ``navigate`` step where it happened,
except for a reload of the page it was already on, and refuses a navigation to an origin the caller
did not allow.
"""

from __future__ import annotations

from typing import Any

import pytest

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.errors import CompileError
from packages.compiler.testing import EMAIL_TARGETS, INPUT_FIELDS, SAMPLE_ROW, action_event
from packages.compiler.validation import validation_errors

HOME_URL = "https://crm.example.com/"
PARTNER_URL = "https://partners.example.com/"
OFF_ORIGIN_URL = "https://evil.example.com/records/new"
HOME_ORIGIN = "https://crm.example.com"

PARTNER_TARGETS: list[dict[str, Any]] = [
    {"by": "role", "role": "link", "name": "Open partner site"},
    {"by": "testid", "value": "partner-link"},
]
NEW_LEAD_TARGETS: list[dict[str, Any]] = [
    {"by": "role", "role": "button", "name": "New lead"},
    {"by": "testid", "value": "new-lead"},
]


def navigate_event(seq: int, url: str) -> dict[str, Any]:
    """A recorded top-level navigation: a url, and no action."""
    return {"seq": seq, "kind": "navigate", "url": url}


def options() -> CompileOptions:
    return CompileOptions(
        name="Create a record from a sheet",
        when_to_use="Each row has to exist in the application exactly once.",
        sample_row=dict(SAMPLE_ROW),
        input_fields={name: dict(field) for name, field in INPUT_FIELDS.items()},
        record_key_column="email",
    )


def test_a_back_navigation_compiles_to_a_navigate_step_at_its_position() -> None:
    """home -> partner link -> back to home -> New lead keeps the recorded move home."""
    recording = [
        navigate_event(1, HOME_URL),
        action_event(
            2, "click", "click", PARTNER_TARGETS, name="Click Open partner site", url=HOME_URL
        ),
        navigate_event(3, HOME_URL),
        action_event(4, "click", "click", NEW_LEAD_TARGETS, name="Click New lead", url=HOME_URL),
    ]

    result = compile_recording(recording, options())

    steps = result.recipe["steps"]
    assert [step["action"] for step in steps] == ["navigate", "click", "navigate", "click"]
    assert steps[2]["url"] == HOME_URL


def test_an_off_origin_navigation_is_a_compile_problem() -> None:
    """A navigate whose origin is not in the pinned allowed_origins is refused, naming the url."""
    recording = [
        action_event(
            1, "click", "click", PARTNER_TARGETS, name="Click Open partner site", url=HOME_URL
        ),
        navigate_event(2, OFF_ORIGIN_URL),
    ]
    pinned = options()
    pinned.allowed_origins = [HOME_ORIGIN]

    with pytest.raises(CompileError) as excinfo:
        compile_recording(recording, pinned)

    assert OFF_ORIGIN_URL in str(excinfo.value)
    assert any(OFF_ORIGIN_URL in problem for problem in excinfo.value.problems)


def test_a_reload_of_the_same_url_gives_no_step() -> None:
    """Two navigations to the same url with nothing between them are one page load."""
    recording = [navigate_event(1, HOME_URL), navigate_event(2, HOME_URL)]

    result = compile_recording(recording, options())

    assert [step["action"] for step in result.recipe["steps"]] == ["navigate"]


def test_an_action_then_reload_also_gives_no_step() -> None:
    """A fill and then a navigation to the same url is a reload, so it adds no step."""
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com", url=HOME_URL),
        navigate_event(2, HOME_URL),
    ]

    result = compile_recording(recording, options())

    assert [step["action"] for step in result.recipe["steps"]] == ["navigate", "fill"]


def test_a_click_that_navigates_yields_no_navigate_step_of_its_own() -> None:
    """A navigation the click caused is not recorded again: the click is the only move."""
    recording = [
        action_event(
            1, "click", "click", PARTNER_TARGETS, name="Click Open partner site", url=HOME_URL
        ),
    ]

    result = compile_recording(recording, options())

    assert [step["action"] for step in result.recipe["steps"]] == ["navigate", "click"]


def test_a_navigate_step_validates_against_the_schema() -> None:
    """A navigate step carries only id, action, name and url, so the schema accepts it."""
    recording = [
        navigate_event(1, HOME_URL),
        action_event(2, "click", "click", NEW_LEAD_TARGETS, name="Click New lead", url=HOME_URL),
    ]

    result = compile_recording(recording, options())

    assert validation_errors(result.recipe) == []
