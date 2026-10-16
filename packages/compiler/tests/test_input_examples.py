"""PL-028: an input field can carry the example value the task was taught with.

The example is the compiler's memory of ``sample_row``: the web app never has the taught values,
so the batch check matches a spreadsheet column by value only when the recipe itself carries
them.
"""

from __future__ import annotations

from typing import Any

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.testing import EMAIL_TARGETS, SAVE_TARGETS, action_event
from packages.compiler.validation import validation_errors


def compile_with_sample_row(sample_row: dict[str, Any]) -> Any:
    """Compile a small create recording with the schema derived from ``sample_row``."""
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="a@b.test"),
        action_event(2, "click", "click", SAVE_TARGETS),
        action_event(3, "submit", "click", SAVE_TARGETS, observation={"text_visible": "Saved"}),
    ]
    options = CompileOptions(
        name="n",
        when_to_use="w",
        sample_row=sample_row,
        record_key_column="email",
    )
    return compile_recording(recording, options)


def test_the_sample_value_becomes_the_fields_example() -> None:
    """A column's sample value rides the field as ``example``; an empty one does not."""
    result = compile_with_sample_row({"email": "a@b.test", "note": ""})
    fields = result.recipe["meta"]["input_schema"]["fields"]
    assert fields["email"]["example"] == "a@b.test"
    assert "example" not in fields["note"]


def test_an_example_is_cut_to_two_hundred_characters() -> None:
    fields = compile_with_sample_row({"email": "x" * 250}).recipe["meta"]["input_schema"]["fields"]
    assert fields["email"]["example"] == "x" * 200


def test_the_compiled_recipe_with_examples_validates() -> None:
    result = compile_with_sample_row({"email": "a@b.test"})
    assert validation_errors(result.recipe) == []


def test_the_schema_refuses_a_non_string_example() -> None:
    result = compile_with_sample_row({"email": "a@b.test"})
    result.recipe["meta"]["input_schema"]["fields"]["email"]["example"] = 5
    assert validation_errors(result.recipe) != []
