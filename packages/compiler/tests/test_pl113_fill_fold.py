"""PL-113 item 3, at the compiler: several fills into one field are one line, with the last value.

The owner's recording carried the sales price three times -- "900.00", then "910", then "910.00" --
as three separate steps. A person reading the recipe saw the same field changed three times, and a
replay typed all three. The fold keeps the last one.
"""

from __future__ import annotations

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.testing import INTEREST_TARGETS, SAVE_TARGETS, action_event

URL = "https://app.example.com/products/1"


def _recording() -> list[dict]:
    return [
        action_event(1, "fill", "fill", INTEREST_TARGETS, value="900.00", url=URL),
        action_event(2, "fill", "fill", INTEREST_TARGETS, value="910", url=URL),
        action_event(3, "fill", "fill", INTEREST_TARGETS, value="910.00", url=URL),
        action_event(
            4,
            "click",
            "click",
            SAVE_TARGETS,
            url=URL,
            observation={"text_visible": "Saved"},
        ),
    ]


def _options() -> CompileOptions:
    return CompileOptions(
        name="Update a price",
        when_to_use="Each row changes one price.",
        sample_row={"price": "910.00"},
        input_fields={"price": {"type": "string"}},
        record_key="{{row.price}}",
        commit_step_ids=["click-4"],
    )


def test_three_fills_into_one_field_are_one_line_with_the_last_value() -> None:
    result = compile_recording(_recording(), _options())

    fills = [step for step in result.recipe["steps"] if step["action"] == "fill"]
    assert len(fills) == 1
    # The one line is the row's own column, and it carries the last value the person typed (910.00
    # is the sample row's cell, which is what parameterising matched).
    assert fills[0]["value"] == {"kind": "input", "column": "price"}
