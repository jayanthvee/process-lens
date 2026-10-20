"""PL-060: a recipe remembers how long the person took to do one row by hand.

Every recorded event carries ``at`` (an ISO timestamp). The demonstration is the one real
measurement of manual effort in the whole system, so the compiler records it on the recipe
instead of leaving "time saved" to a hard-coded guess. The number is the span of the entry the
recipe was built from -- the kept events, from the first to the commit (or the last kept event
when there is no commit) -- and it is left out, silently, whenever the recording cannot support
it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.testing import action_event
from packages.compiler.validation import assert_valid_recipe, validation_errors

TWO_SAVES = Path(__file__).parent / "fixtures" / "odoo_two_saves_owner.json"

BASE_URL = "http://127.0.0.1:8069"
SALES_NEW = f"{BASE_URL}/odoo/sales/new"

CUSTOMER_FILL = [{"by": "role", "role": "combobox", "name": "Customer"}]
SAVE_TARGETS = [
    {"by": "role", "role": "button", "name": "Save"},
    {"by": "css", "selector": "button.o_form_button_save"},
]
SAMPLE_ROW: dict[str, str] = {"customer": "Ridgeline Outfitters"}


def _options(**overrides: Any) -> CompileOptions:
    base: dict[str, Any] = {
        "name": "New quotation",
        "when_to_use": "Each row is a sales quotation for one customer.",
        "sample_row": dict(SAMPLE_ROW),
        "record_key_column": "customer",
        "commit_step_ids": ["save-1"],
    }
    base.update(overrides)
    return CompileOptions(**base)


def _at(seconds: float) -> str:
    """An ISO timestamp ``seconds`` after a fixed start, as the recorder writes them."""
    from datetime import UTC, datetime, timedelta

    start = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)
    return (start + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _stamp(event: dict[str, Any], seconds: float | str | None) -> dict[str, Any]:
    """The same recorded event, carrying ``at`` (or the literal value given)."""
    if seconds is None:
        return event
    event["at"] = _at(seconds) if isinstance(seconds, (int, float)) else seconds
    return event


def _entry(
    stamps: tuple[float | str | None, ...] = (0, 20, 95, 130),
) -> list[dict[str, Any]]:
    """Type the customer, add a line, then Save -- one event per given timestamp."""
    filled, second, third, saved = stamps
    return [
        _stamp(
            action_event(
                1, "fill", "fill", CUSTOMER_FILL, value="Ridgeline Outfitters", url=SALES_NEW
            ),
            filled,
        ),
        _stamp(
            action_event(
                2, "fill", "fill", CUSTOMER_FILL, value="Ridgeline Outfitters", url=SALES_NEW
            ),
            second,
        ),
        _stamp(action_event(3, "click", "click", SAVE_TARGETS, url=SALES_NEW), third),
        _stamp(
            action_event(4, "click", "click", SAVE_TARGETS, step_id="save-1", url=SALES_NEW),
            saved,
        ),
    ]


def _compile(events: list[dict[str, Any]], **overrides: Any) -> Any:
    return compile_recording(events, _options(**overrides))


def test_the_demonstration_measures_one_row() -> None:
    """Events 0, 20, 95 and 130 seconds apart, save at 130: the row took 130 seconds."""
    result = _compile(_entry())

    assert result.recipe["meta"]["manual_seconds_per_row"] == 130
    assert "one row took about 130 seconds by hand" in result.warnings


def test_a_two_save_recording_measures_the_entry_the_recipe_came_from() -> None:
    """PL-044's fixture, trimmed to its first entry: only that entry's span is measured.

    The first kept step is the click at 01:30:28.313 and the commit (``click-8``) is at
    01:31:08.330 -- 40.017 seconds, rounded to 40.
    """
    events = [
        event for event in json.loads(TWO_SAVES.read_text(encoding="utf-8")) if event["seq"] <= 24
    ]
    options = CompileOptions(
        name="Two saves lead",
        when_to_use="test",
        sample_row={
            "contact": "Probe Five Contact",
            "email": "probe.five.example.test",
            "phone": "5550000055",
            "expected_revenue": "3820",
        },
        record_key_column="email",
        commit_step_ids=["click-8"],
        allowed_origins=["http://localhost:8069"],
    )
    result = compile_recording(events, options)

    assert result.recipe["meta"]["manual_seconds_per_row"] == 40


@pytest.mark.parametrize(
    "stamps",
    [
        pytest.param((None, None, None, None), id="no-at-values"),
        pytest.param(("not a time", "also not a time", None, None), id="garbage"),
        pytest.param((0, None, None, None), id="only-one-parseable"),
        # The measurement ends at the commit; a commit with no usable time cannot be measured
        # to, even though earlier steps have times.
        pytest.param((0, 20, 95, None), id="commit-without-at"),
    ],
)
def test_unusable_timestamps_leave_the_field_out(
    stamps: tuple[float | str | None, ...],
) -> None:
    result = _compile(_entry(stamps))

    assert "manual_seconds_per_row" not in result.recipe["meta"]
    assert not any("seconds by hand" in warning for warning in result.warnings)
    assert validation_errors(result.recipe) == []


def test_a_span_under_a_second_is_one_second() -> None:
    result = _compile(_entry((0, 0.1, 0.2, 0.4)))
    assert result.recipe["meta"]["manual_seconds_per_row"] == 1


def test_a_span_over_a_day_is_left_out() -> None:
    result = _compile(_entry((0, 60, 120, 25 * 60 * 60)))

    assert "manual_seconds_per_row" not in result.recipe["meta"]
    assert not any("seconds by hand" in warning for warning in result.warnings)


def test_a_negative_span_is_left_out() -> None:
    result = _compile(_entry((130, 95, 20, 0)))
    assert "manual_seconds_per_row" not in result.recipe["meta"]


def test_with_no_commit_the_last_kept_event_ends_the_span() -> None:
    """No commit-capable step: the span runs from the first kept event to the last one."""
    events = [
        _stamp(
            action_event(
                1, "fill", "fill", CUSTOMER_FILL, value="Ridgeline Outfitters", url=SALES_NEW
            ),
            0,
        ),
        _stamp(
            action_event(
                2, "fill", "fill", CUSTOMER_FILL, value="Ridgeline Outfitters", url=SALES_NEW
            ),
            20,
        ),
        _stamp(
            action_event(3, "click", "click", SAVE_TARGETS, step_id="other-1", url=SALES_NEW),
            95,
        ),
    ]
    result = _compile(events, commit_step_ids=[])

    assert result.recipe["meta"]["manual_seconds_per_row"] == 95


def test_the_field_validates_against_the_schema() -> None:
    result = _compile(_entry())
    assert_valid_recipe(result.recipe)
    assert validation_errors(result.recipe) == []
