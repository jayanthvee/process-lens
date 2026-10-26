"""PL-110: a recorded pick reaches the recipe as a pick, not a plain fill.

The recorder marks the fill (`autocomplete: true`) when a click on an option follows it. The schema
already has the field; this proves the compiler carries it from the envelope onto the step, so the
executor replays it as a pick.
"""

from __future__ import annotations

from typing import Any

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.testing import action_event

URL = "https://app.example.test/records/new"

OPTION = {
    "by": "role",
    "role": "option",
    "name": 'Create "Nadia Rahman"',
}


def recording(*, marked: bool) -> list[dict[str, Any]]:
    fill = action_event(
        1,
        "fill",
        "fill",
        [{"by": "role", "role": "combobox", "name": "Contact"}],
        value="Nadia Rahman",
        name="Fill Contact",
        url=URL,
    )
    if marked:
        fill["autocomplete"] = True
    return [
        fill,
        action_event(
            2,
            "click",
            "click",
            [OPTION],
            name='Click Create "Nadia Rahman"',
            url=URL,
        ),
        action_event(
            3,
            "click",
            "click",
            [{"by": "role", "role": "button", "name": "Save"}],
            name="Click Save",
            url=URL,
        ),
    ]


def compile_it(*, marked: bool) -> Any:
    return compile_recording(
        recording(marked=marked),
        CompileOptions(
            name="Add a contact",
            when_to_use="Add a contact.",
            sample_row={"contact": "Nadia Rahman"},
            input_fields={"contact": {"type": "string", "required": True}},
            record_key_column="contact",
            allowed_origins=["https://app.example.test"],
            include_navigate=False,
            commit_step_ids=["click-3"],
            commit_assertions={"click-3": {"text_visible": "Saved", "timeout_ms": 8000}},
        ),
    )


def _contact_fill(result: Any) -> dict[str, Any]:
    return next(
        step
        for step in result.recipe["steps"]
        if step.get("action") == "fill" and "Contact" in step.get("name", "")
    )


def test_a_marked_fill_carries_the_pick_onto_the_step() -> None:
    assert _contact_fill(compile_it(marked=True)).get("autocomplete") is True


def test_an_unmarked_fill_stays_a_plain_fill() -> None:
    assert "autocomplete" not in _contact_fill(compile_it(marked=False))
