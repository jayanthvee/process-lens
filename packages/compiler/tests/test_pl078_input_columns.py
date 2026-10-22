"""PL-078: two fields taught the same sample value keep their own input columns.

A timesheet demonstration types Monday's ``2:00`` and Tuesday's ``2:00`` -- the same literal in two
different cells. Matching the value alone bound both fills to one column, so a row whose Tuesday
was ``1:00`` replayed Monday's hours into it. The step's own field name decides between equal
columns; a step that names none keeps the old first-match behaviour.
"""

from __future__ import annotations

from packages.compiler.model import Candidate
from packages.compiler.parameterize import parameterize_candidate


def _fill(step_id: str, name: str, rung_name: str, value: str) -> Candidate:
    return Candidate(
        action="fill",
        name=name,
        targets=[{"by": "role", "role": "textbox", "name": rung_name}],
        value={"kind": "constant", "value": value},
        seq=int(step_id),
        recorded_id=step_id,
    )


def test_two_day_cells_with_the_same_sample_hours_keep_their_own_columns() -> None:
    sample_row = {"f_19mon": "2:00", "f_20tue": "2:00"}
    monday = _fill("2", "Fill 19Mon", "19Mon", "2:00")
    tuesday = _fill("3", "Fill 20Tue", "20Tue", "2:00")

    assert parameterize_candidate(monday, sample_row)[0].value == {
        "kind": "input",
        "column": "f_19mon",
    }
    assert parameterize_candidate(tuesday, sample_row)[0].value == {
        "kind": "input",
        "column": "f_20tue",
    }


def test_a_step_that_names_no_matching_field_still_binds_the_first_equal_column() -> None:
    sample_row = {"f_19mon": "2:00", "f_20tue": "2:00"}
    unnamed = _fill("2", "Fill hours", "Hours", "2:00")

    assert parameterize_candidate(unnamed, sample_row)[0].value == {
        "kind": "input",
        "column": "f_19mon",
    }


def test_a_value_held_by_one_column_is_bound_whatever_the_step_is_called() -> None:
    sample_row = {"f_21wed": "1:30"}
    wednesday = _fill("4", "Fill 21Wed", "21Wed", "1:30")

    assert parameterize_candidate(wednesday, sample_row)[0].value == {
        "kind": "input",
        "column": "f_21wed",
    }
