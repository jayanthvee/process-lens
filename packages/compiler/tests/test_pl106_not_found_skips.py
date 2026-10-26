"""PL-106 item 7: an update whose search finds nothing parks the row, and a person can skip it.

PL-077 parked such a row. PL-106 first ended it ``skipped``, but T1's review sent that back: the
branch's ``element_absent`` condition can hold before a slow results table has drawn, and on a
login wall, an expired-session dialog or an error page -- the executor's wall probe only runs when
a check answers false. A skip is final, so that would lose the row for good. The branch parks
instead, with the reason "Not found in <site>." where <site> is the host the search ran on. The run
page then offers a one-click Skip it through the existing parked -> skipped dismiss.

The file keeps the ticket's name; the review changed the branch's outcome, not the item.

Ledger: the branch runs after the search and before any commit step, so the record is in
``filling`` with ``commit_attempted`` false. ``filling -> parked`` is legal, and the person's skip
is the ordinary ``parked -> skipped`` dismiss (migration 0003, LEDGER_SPEC). No ledger change is
needed.
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


def test_a_search_that_finds_nothing_parks_the_row_and_names_the_site() -> None:
    result = _compile()

    branch = _branches(result.recipe["steps"])[0]
    assert branch["then"] == [
        {
            "id": "end-record-1",
            "action": "end_record",
            "outcome": "parked",
            "reason": "Not found in crm.example.com.",
        }
    ]
    assert branch["name"] == "Park a record when the search finds nothing"


def test_the_compile_warning_tells_a_person_what_happens_in_plain_words() -> None:
    warnings = [line for line in _compile().warnings if "search finds the record to change" in line]

    assert warnings == [PLAIN_WARNING]


def test_a_search_page_with_no_host_still_says_not_found_in() -> None:
    """``rowOutcome`` reads this prefix; an empty host must not change that (PL-106 review).

    The search's own step carries no URL; the other steps still name the site, so the compile can
    derive ``allowed_origins`` and the host lookup has nothing to read.
    """
    events = _find_then_update()
    events[1]["url"] = ""

    result = compile_recording(events, _options(commit_step_ids=["save-1"]))

    branch = _branches(result.recipe["steps"])[0]
    reason = branch["then"][0]["reason"]
    assert branch["then"][0]["outcome"] == "parked"
    assert reason == "Not found in the app."
    assert reason.startswith("Not found in")
