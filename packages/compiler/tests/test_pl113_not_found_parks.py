"""PL-113 item 6: a search that finds nothing skips the row and says which value was missing.

PL-106 parked such a row (its review was worried a skip is final). The ticket asks for a skip in
plain words: the column's name and the row's value. ``filling -> skipped`` is legal here because
nothing was committed: the search is a read.
"""

from __future__ import annotations

from packages.compiler import compile_recording
from packages.compiler.tests.test_pl077_search_shape import (
    _branches,
    _find_then_update,
    _options,
)


def _compile():
    return compile_recording(_find_then_update(), _options(commit_step_ids=["save-1"]))


def test_a_search_that_finds_nothing_skips_the_row_in_plain_words() -> None:
    branch = _branches(_compile().recipe["steps"])[0]

    end = branch["then"][0]
    assert end["outcome"] == "skipped"
    assert end["reason"] == "No sku called ‘{{row.sku}}’ was found."


def test_the_skip_names_the_column_and_the_row_value() -> None:
    """Rendered with the ticket's own row: the column's plain name, and the value searched for."""
    from services.runner.templates import render

    branch = _branches(_compile().recipe["steps"])[0]
    sentence = render(
        branch["then"][0]["reason"],
        row={"sku": "Ceramic Coffee Mug", "price": "12"},
        variables={},
    )

    assert sentence == "No sku called ‘Ceramic Coffee Mug’ was found."


def test_the_compile_warning_says_the_row_is_skipped() -> None:
    warnings = [line for line in _compile().warnings if "search finds the record to change" in line]

    assert warnings == [
        "the search finds the record to change; if it finds nothing, the row is skipped"
    ]
