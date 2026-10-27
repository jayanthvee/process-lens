"""PL-106 item 7, restored by T1's review of PL-113 (B2): a search that finds nothing parks.

PL-106 first ended such a row ``skipped``; T1's review sent that back and it parked instead, with
the site named ("Not found in <site>."), because ``element_absent`` can hold before a slow results
table has drawn, on a login wall or on an error page, and a skip is final.

PL-113 item 6 changed it to a skip again. T1's review of PL-113 called that the ticket's error and
sent it back: the row parks, and the run page's **Skip it** button is how a person turns it into a
skip after looking. What PL-113 added and this file keeps is the plain sentence -- the column's own
name and the value the row searched for -- so the row says exactly what was not found.

``filling -> parked`` is legal here: the search is a read and nothing was committed, and the
person's skip is the ordinary ``parked -> skipped`` dismiss (migration 0003).
"""

from __future__ import annotations

from packages.compiler import compile_recording
from packages.compiler.tests.test_pl077_search_shape import _branches, _find_then_update, _options

# What a person reads under Save: plain words, no branch or outcome names.
PLAIN_WARNING = (
    "the search finds the record to change; if it finds nothing, the row is left for you to skip"
)


def _compile():
    return compile_recording(_find_then_update(), _options(commit_step_ids=["save-1"]))


def test_a_search_that_finds_nothing_parks_the_row_and_names_the_value() -> None:
    branch = _branches(_compile().recipe["steps"])[0]

    assert branch["then"] == [
        {
            "id": "end-record-1",
            "action": "end_record",
            "outcome": "parked",
            "reason": "No sku called ‘{{row.sku}}’ was found.",
        }
    ]
    assert branch["name"] == "Park a record when the search finds nothing"


def test_the_compile_warning_tells_a_person_what_happens_in_plain_words() -> None:
    warnings = [line for line in _compile().warnings if "search finds the record to change" in line]

    assert warnings == [PLAIN_WARNING]


def test_the_sentence_is_the_tickets_shape_and_the_row_is_parked() -> None:
    """The column's plain name and the row's own value, not the site and not a field's hint."""
    from services.runner.templates import render

    branch = _branches(_compile().recipe["steps"])[0]
    sentence = render(branch["then"][0]["reason"], row={"sku": "Ceramic Coffee Mug"}, variables={})

    assert sentence == "No sku called ‘Ceramic Coffee Mug’ was found."
    assert sentence.startswith("No ")
    assert sentence.endswith(" was found.")
    assert branch["then"][0]["outcome"] == "parked"
