"""PL-103: one bad step never makes the whole save fail with a reason nobody can read.

On the pilot call a click on Odoo's form background was recorded as a step named with the whole
form's text. "Click " in front of it went past the schema's 120 characters, and the save failed with
"is not valid under any of the given schemas", shown only under Notes for support.
"""

from __future__ import annotations

from packages.compiler import compile_recording
from packages.compiler.compiler import plain_step_problems
from packages.compiler.tests.test_pl027_real_apps import click, fill, options

LONG = "New lead Fill in the details of the opportunity, then save it to the pipeline. " * 4


def test_a_long_step_name_is_cut_to_fit_and_the_task_saves() -> None:
    recording = [
        fill(1, "fill-1", "e.g. Product Pricing", "Demo Lead"),
        click(2, "click-1", LONG),
        click(3, "click-2", "Save manually"),
    ]

    result = compile_recording(recording, options())

    names = [step["name"] for step in result.recipe["steps"] if "name" in step]
    assert names, "the recipe has named steps"
    assert all(len(name) <= 120 for name in names), names


def test_a_refused_step_is_named_in_plain_words_first() -> None:
    recipe = {"steps": [{"name": "Open the page"}, {"name": "Click the form"}]}
    problems = ["steps/1: {'id': 'x'} is not valid under any of the given schemas"]

    plain = plain_step_problems(recipe, problems)

    assert plain[0] == (
        'Step 2 ("Click the form") can\'t be saved as it was recorded. '
        "Remove it with × and save again."
    )
    assert plain[1:] == problems, "the engine's own lines stay for support"


def test_problems_outside_the_steps_are_left_as_they_are() -> None:
    problems = ["meta/name: '' is too short"]

    assert plain_step_problems({"steps": []}, problems) == problems
