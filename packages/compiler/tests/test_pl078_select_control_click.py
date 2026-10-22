"""PL-078: a click that opened a dropdown is not replayed in front of the select that opens it.

The recorder writes one click step for pressing the control that opens a custom dropdown, and one
select step for pressing the option (no `click` event fires for the option: the widget selects on
`mousedown` and closes the list). On replay the select opens the control itself, so a click kept in
front of it toggles the list open and the select's own click closes it again.
"""

from __future__ import annotations

from packages.compiler.compiler import CompileOptions, compile_recording

PAGE = "https://hr.example.test/time/edit"


def _event(seq: int, kind: str, step: dict | None, observation: dict | None = None) -> dict:
    return {
        "seq": seq,
        "at": "2026-10-21T00:00:00.000Z",
        "kind": kind,
        "url": PAGE,
        "step": step,
        **({"observation": observation} if observation else {}),
    }


def _recording() -> list[dict]:
    control = [
        {"by": "role", "role": "generic", "name": "-- Select --"},
        {"by": "css", "selector": "div.oxd-select-text-input"},
    ]
    option = [
        {"by": "role", "role": "generic", "name": "Development"},
        {"by": "css", "selector": "div.oxd-select-text-input"},
    ]
    return [
        _event(
            1,
            "click",
            {"id": "click-1", "action": "click", "name": "Click -- Select --", "targets": control},
        ),
        _event(
            2,
            "select",
            {
                "id": "select-1",
                "action": "select",
                "name": "Select Development",
                "targets": option,
                "value": {"kind": "constant", "value": "Development"},
                "option_match": "text",
            },
        ),
        _event(
            3,
            "click",
            {
                "id": "click-2",
                "action": "click",
                "name": "Click Save",
                "targets": [{"by": "role", "role": "button", "name": "Save"}],
                "commit": True,
                "assert_after": {"text_visible": "Successfully Saved"},
            },
        ),
    ]


def _compile() -> tuple[list[dict], list[str]]:
    options = CompileOptions(
        name="Weekly timesheet",
        when_to_use="Weekly timesheet",
        sample_row={"project": "CRM Cleanup", "development": "Development"},
        record_key="{{row.project}}:{{row.development}}",
    )
    result = compile_recording(_recording(), options)
    return list(result.recipe["steps"]), result.warnings


def test_the_click_that_opened_the_dropdown_is_folded_into_its_select() -> None:
    steps, warnings = _compile()
    names = [step.get("name") for step in steps]

    assert "Click -- Select --" not in names, names
    assert "Select Development" in names, names
    assert "Click Save" in names, names
    assert any("folded the click at event 1" in warning for warning in warnings), warnings


def test_a_click_on_some_other_control_is_kept() -> None:
    recording = _recording()
    recording[0]["step"]["targets"] = [{"by": "role", "role": "button", "name": "Add row"}]

    options = CompileOptions(
        name="Weekly timesheet",
        when_to_use="Weekly timesheet",
        sample_row={"project": "CRM Cleanup", "development": "Development"},
        record_key="{{row.project}}:{{row.development}}",
    )
    result = compile_recording(recording, options)
    names = [step.get("name") for step in result.recipe["steps"]]

    assert "Click -- Select --" in names, names
