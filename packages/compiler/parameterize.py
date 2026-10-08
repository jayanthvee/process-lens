"""Turn recorded literals into the variables of a reusable workflow.

A demonstration is recorded once, with one record's real values typed in. To run
it for every record those values have to become references to the input row. Two
places carry them:

* a step's value — a literal that equals a cell of the sample row becomes the
  input column itself, so the step reads that column at run time;
* a target rung's text — a literal embedded in an accessible name or a test id
  (a search result cell, a row's link) becomes a ``{{row.column}}`` template.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .model import Candidate

# A rung field whose value is user-facing text that may contain a recorded value.
# ``role`` is a fixed vocabulary and ``selector`` is CSS, so neither is templated.
TEMPLATE_RUNG_FIELDS = ("name", "value", "text")

# Short literals ("1", "a") match too much to parameterize safely.
MIN_MATCH_LENGTH = 3


def _cells(sample_row: dict[str, Any]) -> list[tuple[str, str]]:
    """The sample row's non-empty cells, as (column, text), longest text first."""
    cells = [
        (column, str(value).strip())
        for column, value in sample_row.items()
        if value is not None and str(value).strip()
    ]
    cells.sort(key=lambda cell: len(cell[1]), reverse=True)
    return cells


def match_column(value: Any, sample_row: dict[str, Any]) -> str | None:
    """The input column whose sample value equals this literal, if any."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    matches = [column for column, cell in _cells(sample_row) if cell == text]
    return matches[0] if matches else None


def parameterize_value(value: Any, sample_row: dict[str, Any]) -> tuple[Any, str | None]:
    """Replace a literal value with a reference to the input column it came from."""
    if not isinstance(value, dict) or value.get("kind") != "constant":
        return value, None
    column = match_column(value.get("value"), sample_row)
    if column is None:
        return value, None
    return {"kind": "input", "column": column}, f"bound a literal value to column {column!r}"


def parameterize_text(text: str, sample_row: dict[str, Any]) -> tuple[str, str | None]:
    """Replace recorded literals embedded in a rung's text with templates."""
    result = text
    replaced: set[str] = set()
    for column, cell in _cells(sample_row):
        if len(cell) < MIN_MATCH_LENGTH or cell not in result:
            continue
        result = result.replace(cell, f"{{{{row.{column}}}}}")
        replaced.add(column)
    if not replaced:
        return text, None
    return result, f"templated {len(replaced)} literal(s) in a target rung"


def parameterize_targets(
    targets: list[dict[str, Any]],
    sample_row: dict[str, Any],
    *,
    exclude_text: Any = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Parameterize the text of every rung that can carry a recorded literal.

    ``exclude_text`` is a literal not to template, used for the value a step is
    itself entering: a rung that echoes it describes the value, not the element.
    """
    if not sample_row:
        return targets, []
    excluded = exclude_text.strip() if isinstance(exclude_text, str) else None
    updated: list[dict[str, Any]] = []
    warnings: list[str] = []
    for rung in targets:
        rung = dict(rung)
        for field_name in TEMPLATE_RUNG_FIELDS:
            current = rung.get(field_name)
            if not isinstance(current, str) or current == excluded:
                continue
            new_text, note = parameterize_text(current, sample_row)
            if note:
                rung[field_name] = new_text
                warnings.append(note)
        updated.append(rung)
    return updated, warnings


def parameterize_candidate(
    candidate: Candidate, sample_row: dict[str, Any]
) -> tuple[Candidate, list[str]]:
    """Bind a candidate's literal value and rung text to the sample row.

    A rung whose text is exactly this step's own recorded value is skipped. For a
    fill that rung echoes the value being typed in (a text field's current
    content); it is not the element's own text, and templating it would produce a
    locator that cannot match before the value is set.
    """
    warnings: list[str] = []

    literal = None
    if isinstance(candidate.value, dict) and candidate.value.get("kind") == "constant":
        literal = candidate.value.get("value")

    targets, target_warnings = parameterize_targets(
        candidate.targets, sample_row, exclude_text=literal
    )
    warnings.extend(target_warnings)

    value = candidate.value
    if value is not None:
        value, note = parameterize_value(value, sample_row)
        if note:
            warnings.append(note)

    return replace(candidate, targets=targets, value=value), warnings
