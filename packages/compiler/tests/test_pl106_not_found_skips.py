"""PL-106 item 7: an update whose search finds nothing is skipped, not parked.

PL-077 parked such a row ("needs help"), but there is nothing for a person to do: the record is not
in the app, so the row has nothing to change. It ends ``skipped`` with the reason "Not found in
<site>." -- the site being the host the search ran on.

Ledger: the branch runs after the search and before any commit step, so the record is in
``filling`` with ``commit_attempted`` false. ``filling -> skipped`` is legal for exactly that case
(migration 0003, LEDGER_SPEC row "filling | skipped"), and the runner's end_record already turns a
commit-attempted skip into a park (B2). No ledger change is needed.
"""

from __future__ import annotations

from packages.compiler import compile_recording
from packages.compiler.tests.test_pl077_search_shape import _branches, _find_then_update, _options


def test_a_search_that_finds_nothing_skips_the_row_and_names_the_site() -> None:
    result = compile_recording(_find_then_update(), _options(commit_step_ids=["save-1"]))

    branch = _branches(result.recipe["steps"])[0]
    assert branch["then"] == [
        {
            "id": "end-record-1",
            "action": "end_record",
            "outcome": "skipped",
            "reason": "Not found in crm.example.com.",
        }
    ]
    assert branch["name"] == "Skip a record when the search finds nothing"
