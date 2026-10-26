"""PL-107 item 16: a Create "<value>" option follows the row's value, and an existing record is
accepted first -- on a recording that is not Odoo's.

The compiler templates the value the person typed into the field just before the click, so the
option name no longer carries the sample row's value, and it offers the exact option first so an
existing record is clicked instead of created.
"""

from __future__ import annotations

from typing import Any

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.testing import action_event

URL = "https://app.example.test/records/new"


def recording() -> list[dict[str, Any]]:
    return [
        action_event(
            1,
            "fill",
            "fill",
            [{"by": "role", "role": "combobox", "name": "Contact"}],
            value="tester 1",
            name="Fill Contact",
            url=URL,
        ),
        action_event(
            2,
            "click",
            "click",
            [
                {"by": "role", "role": "option", "name": 'Create "tester 1"'},
                {"by": "css", "selector": "#contact-option-1"},
            ],
            name='Click Create "tester 1"',
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


def compile_it() -> Any:
    return compile_recording(
        recording(),
        CompileOptions(
            name="Add a contact",
            when_to_use="Add a contact to the list.",
            sample_row={"contact": "tester 1"},
            input_fields={"contact": {"type": "string", "required": True}},
            record_key_column="contact",
            allowed_origins=["https://app.example.test"],
            include_navigate=False,
            commit_step_ids=["click-3"],
            commit_assertions={"click-3": {"text_visible": "Saved", "timeout_ms": 8000}},
        ),
    )


def test_the_create_option_name_is_templated_with_the_row_value() -> None:
    """The step must not keep the sample row's value; it names ``Create "<the row's contact>"``."""
    result = compile_it()
    create = next(
        step
        for step in result.recipe["steps"]
        if step.get("action") == "click" and "Create" in step.get("name", "")
    )
    names = [rung.get("name") for rung in create["targets"] if rung.get("by") == "role"]
    assert 'Create "tester 1"' not in names
    assert 'Create "{{row.contact}}"' in names


def test_the_exact_option_is_offered_before_the_create_option() -> None:
    """An existing record is clicked (rung 1); only a missing one is created (rung 2)."""
    result = compile_it()
    create = next(
        step
        for step in result.recipe["steps"]
        if step.get("action") == "click" and "Create" in step.get("name", "")
    )
    role_rungs = [rung for rung in create["targets"] if rung.get("by") == "role"]
    assert role_rungs[0] == {"by": "role", "role": "option", "name": "{{row.contact}}"}
    assert role_rungs[1] == {
        "by": "role",
        "role": "option",
        "name": 'Create "{{row.contact}}"',
    }
