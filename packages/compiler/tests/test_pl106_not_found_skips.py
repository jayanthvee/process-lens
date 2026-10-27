"""PL-106 item 7 (as changed by PL-113 item 6): a search that finds nothing ends the row.

PL-106 first ended such a row ``skipped``; T1's review sent that back and it parked instead, with
the site named ("Not found in <site>."), because ``element_absent`` can hold before a slow results
table has drawn, on a login wall or on an error page, and a skip is final.

PL-113 item 6 asks for the skip again, in plainer words: the column's own name and the value the
row searched for ("No product called ‘Ceramic Coffee Mug’ was found."). The ticket is the later
decision, so this file now pins the skip. What the review was worried about is still visible to a
person: the row says exactly which value was not found, so a page that never loaded reads
differently from a search that really found nothing.

The file keeps its name. ``filling -> skipped`` is legal here: the search is a read, and the record
has not attempted a commit (migration 0003).
"""

from __future__ import annotations

from packages.compiler import compile_recording
from packages.compiler.tests.test_pl077_search_shape import _branches, _find_then_update, _options

# What a person reads under Save: plain words, no branch or outcome names.
PLAIN_WARNING = "the search finds the record to change; if it finds nothing, the row is skipped"


def _compile():
    return compile_recording(_find_then_update(), _options(commit_step_ids=["save-1"]))


def test_a_search_that_finds_nothing_skips_the_row_and_names_the_value() -> None:
    branch = _branches(_compile().recipe["steps"])[0]

    assert branch["then"] == [
        {
            "id": "end-record-1",
            "action": "end_record",
            "outcome": "skipped",
            "reason": "No sku called ‘{{row.sku}}’ was found.",
        }
    ]
    assert branch["name"] == "Skip a record when the search finds nothing"


def test_the_compile_warning_tells_a_person_what_happens_in_plain_words() -> None:
    warnings = [line for line in _compile().warnings if "search finds the record to change" in line]

    assert warnings == [PLAIN_WARNING]


def test_the_skip_sentence_is_the_tickets_shape() -> None:
    """The column's plain name and the row's own value, not the site and not a field's hint."""
    from services.runner.templates import render

    reason = _branches(_compile().recipe["steps"])[0]["then"][0]["reason"]
    sentence = render(reason, row={"sku": "Ceramic Coffee Mug"}, variables={})

    assert sentence == "No sku called ‘Ceramic Coffee Mug’ was found."
    assert sentence.startswith("No ")
    assert sentence.endswith(" was found.")
