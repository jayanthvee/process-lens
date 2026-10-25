"""PL-096: a suggestion option chosen by the row's value never falls back to a fixed position.

The pilot's lead task clicked the salesperson suggestion with this ladder (compiled on Render):

    role option "{{row.salesperson}}" -> css "#user_id_0_0_0" -> text_fuzzy "{{row.salesperson}}"

The css rung is the *first* option in the list, whoever it is. A row naming a salesperson the
list does not have ("Priya Ramen") misses the role rung, and the css rung then clicks the first
suggestion: a lead saved with someone else as its salesperson. In the pilot the click only failed
because the option was covered.
"""

from __future__ import annotations

from packages.compiler.model import Candidate
from packages.compiler.parameterize import parameterize_candidate

ROW = {"name": "Ana Ruiz", "salesperson": "Priya Raman"}


def _option_click(*rungs: dict) -> Candidate:
    return Candidate(action="click", targets=list(rungs), name="Click Priya Raman")


def test_an_option_picked_by_the_rows_value_keeps_only_the_rungs_that_name_it() -> None:
    candidate = _option_click(
        {"by": "role", "role": "option", "name": "Priya Raman"},
        {"by": "css", "selector": "#user_id_0_0_0"},
        {"by": "text_fuzzy", "value": "Priya Raman"},
    )

    updated, warnings = parameterize_candidate(candidate, ROW)

    assert updated.targets == [
        {"by": "role", "role": "option", "name": "{{row.salesperson}}"},
        {"by": "text_fuzzy", "value": "{{row.salesperson}}"},
    ]
    assert any("#user_id_0_0_0" in warning for warning in warnings)


def test_a_testid_on_a_templated_option_is_dropped_too() -> None:
    """A test id on an option is as positional as a css path: it belongs to the recorded option."""
    candidate = _option_click(
        {"by": "role", "role": "option", "name": "Priya Raman"},
        {"by": "testid", "value": "option-0"},
    )

    updated, _ = parameterize_candidate(candidate, ROW)

    assert updated.targets == [{"by": "role", "role": "option", "name": "{{row.salesperson}}"}]


def test_an_option_that_names_a_fixed_choice_keeps_its_whole_ladder() -> None:
    """ "Search More..." is not a row value: nothing is templated, so nothing is dropped."""
    rungs = [
        {"by": "role", "role": "option", "name": "Search More..."},
        {"by": "css", "selector": "#user_id_0_0_9"},
    ]

    updated, _ = parameterize_candidate(_option_click(*rungs), ROW)

    assert updated.targets == rungs


def test_an_ordinary_button_with_a_templated_name_keeps_its_fixed_rungs() -> None:
    """Only options: the row-named Save button's css rung still finds the right button."""
    rungs = [
        {"by": "role", "role": "button", "name": "Save Ana Ruiz"},
        {"by": "css", "selector": "button.o_form_button_save"},
    ]
    candidate = Candidate(action="click", targets=rungs, name="Click Save")

    updated, _ = parameterize_candidate(candidate, ROW)

    assert updated.targets == [
        {"by": "role", "role": "button", "name": "Save {{row.name}}"},
        {"by": "css", "selector": "button.o_form_button_save"},
    ]
