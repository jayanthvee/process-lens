"""PL-113 item 6, as corrected by T1's review (B2): a search that finds nothing parks the row.

PL-106's review chose park on purpose: ``element_absent`` also holds before a slow results table has
drawn, on a login wall or on an error page, and a skip is final -- so a skip there would lose the
row. PL-113 item 6 asked for "Skipped", which was the ticket's error, not the implementation's
(T1's words). The plain sentence the ticket asked for is kept: it names the column and the value the
row searched for. The row parks, and the run page's existing **Skip it** button turns it into a
skip after a person has looked.

The file was renamed from ``test_pl113_not_found_skips.py``; the item is now "parks, saying plainly
which value was not found".
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


def test_a_search_that_finds_nothing_parks_the_row_in_plain_words() -> None:
    branch = _branches(_compile().recipe["steps"])[0]

    end = branch["then"][0]
    assert end["outcome"] == "parked"
    assert end["reason"] == "No sku called ‘{{row.sku}}’ was found."


def test_the_sentence_names_the_column_and_the_row_value() -> None:
    """Rendered with the ticket's own row: the column's plain name, and the value searched for."""
    from services.runner.templates import render

    branch = _branches(_compile().recipe["steps"])[0]
    sentence = render(
        branch["then"][0]["reason"],
        row={"sku": "Ceramic Coffee Mug", "price": "12"},
        variables={},
    )

    assert sentence == "No sku called ‘Ceramic Coffee Mug’ was found."


def test_the_compile_warning_says_the_row_is_left_for_the_person() -> None:
    warnings = [line for line in _compile().warnings if "search finds the record to change" in line]

    assert warnings == [
        "the search finds the record to change; if it finds nothing, the row is left for you "
        "to skip"
    ]
