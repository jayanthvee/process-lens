"""Turning recorded literals into references to the input row."""

from __future__ import annotations

from packages.compiler.model import Candidate
from packages.compiler.parameterize import (
    match_column,
    parameterize_candidate,
    parameterize_targets,
    parameterize_text,
    parameterize_value,
)
from packages.compiler.testing import EMAIL_TARGETS, SAMPLE_ROW


def test_a_literal_matching_a_sample_cell_binds_to_that_column() -> None:
    value, note = parameterize_value({"kind": "constant", "value": "ravi@example.com"}, SAMPLE_ROW)
    assert value == {"kind": "input", "column": "email"}
    assert note is not None


def test_a_literal_with_no_match_stays_a_constant() -> None:
    original = {"kind": "constant", "value": "something else"}
    value, note = parameterize_value(original, SAMPLE_ROW)
    assert value == original
    assert note is None


def test_matching_ignores_surrounding_whitespace() -> None:
    value, _ = parameterize_value({"kind": "constant", "value": "  Data course  "}, SAMPLE_ROW)
    assert value == {"kind": "input", "column": "interest"}


def test_matching_is_case_sensitive() -> None:
    value, note = parameterize_value({"kind": "constant", "value": "RAVI@EXAMPLE.COM"}, SAMPLE_ROW)
    assert value == {"kind": "constant", "value": "RAVI@EXAMPLE.COM"}
    assert note is None


def test_a_non_constant_value_is_left_alone() -> None:
    original = {"kind": "page", "variable": "current_price"}
    value, note = parameterize_value(original, SAMPLE_ROW)
    assert value == original
    assert note is None


def test_match_column_finds_the_column_for_a_value() -> None:
    assert match_column("ravi@example.com", SAMPLE_ROW) == "email"
    assert match_column("nobody@example.com", SAMPLE_ROW) is None


def test_a_literal_inside_rung_text_becomes_a_template() -> None:
    text, note = parameterize_text("ravi@example.com", SAMPLE_ROW)
    assert text == "{{row.email}}"
    assert note is not None


def test_a_literal_inside_larger_rung_text_is_replaced_in_place() -> None:
    text, note = parameterize_text("Record ravi@example.com", SAMPLE_ROW)
    assert text == "Record {{row.email}}"
    assert note is not None


def test_rung_text_without_a_recorded_literal_is_untouched() -> None:
    text, note = parameterize_text("Save", SAMPLE_ROW)
    assert text == "Save"
    assert note is None


def test_very_short_literals_are_not_templated() -> None:
    """A three-character floor keeps a stray '1' from matching the whole page."""
    text, note = parameterize_text("1", {"n": "1"})
    assert text == "1"
    assert note is None


def test_parameterizing_targets_rewrites_name_text() -> None:
    targets = [
        {"by": "role", "role": "cell", "name": "ravi@example.com"},
        {"by": "testid", "value": "record-save"},
    ]
    updated, warnings = parameterize_targets(targets, SAMPLE_ROW)
    assert updated[0]["name"] == "{{row.email}}"
    assert updated[1]["value"] == "record-save"
    assert warnings


def test_parameterizing_targets_leaves_role_and_selector_alone() -> None:
    targets = [{"by": "css", "selector": "#ravi@example.com"}]
    updated, _ = parameterize_targets(targets, SAMPLE_ROW)
    assert updated[0]["selector"] == "#ravi@example.com"


def test_parameterizing_with_no_sample_row_changes_nothing() -> None:
    updated, warnings = parameterize_targets(EMAIL_TARGETS, {})
    assert updated == EMAIL_TARGETS
    assert warnings == []


def test_parameterize_candidate_binds_both_value_and_rung_text() -> None:
    candidate = Candidate(
        action="fill",
        targets=[{"by": "role", "role": "cell", "name": "ravi@example.com"}],
        value={"kind": "constant", "value": "Data course"},
    )
    updated, warnings = parameterize_candidate(candidate, SAMPLE_ROW)
    assert updated.value == {"kind": "input", "column": "interest"}
    assert updated.targets[0]["name"] == "{{row.email}}"
    assert warnings


def test_parameterize_candidate_leaves_a_step_with_no_value_alone() -> None:
    candidate = Candidate(action="click", targets=EMAIL_TARGETS, value=None)
    updated, _ = parameterize_candidate(candidate, SAMPLE_ROW)
    assert updated.value is None


def test_a_rung_echoing_the_typed_value_is_not_templated() -> None:
    """A text field's text_fuzzy rung is the value being entered, not a label.

    Templating it would produce a locator that cannot match before the value is
    set, so it is left as recorded for the builder to see and drop.
    """
    candidate = Candidate(
        action="fill",
        targets=[
            {"by": "role", "role": "textbox", "name": "Email"},
            {"by": "text_fuzzy", "value": "ravi@example.com"},
        ],
        value={"kind": "constant", "value": "ravi@example.com"},
    )
    updated, _ = parameterize_candidate(candidate, SAMPLE_ROW)
    assert updated.targets[0]["name"] == "Email"
    assert updated.targets[1]["value"] == "ravi@example.com"
    assert updated.value == {"kind": "input", "column": "email"}


def test_a_click_rung_showing_the_record_value_is_still_templated() -> None:
    """A search result cell carries the record's value, so it must be templated."""
    candidate = Candidate(
        action="click",
        targets=[{"by": "role", "role": "cell", "name": "ravi@example.com"}],
        value=None,
    )
    updated, _ = parameterize_candidate(candidate, SAMPLE_ROW)
    assert updated.targets[0]["name"] == "{{row.email}}"
