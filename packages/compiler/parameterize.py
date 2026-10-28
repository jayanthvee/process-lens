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

import re
from dataclasses import replace
from typing import Any

from .model import Candidate

# A rung field whose value is user-facing text that may contain a recorded value.
# ``role`` is a fixed vocabulary and ``selector`` is CSS, so neither is templated.
TEMPLATE_RUNG_FIELDS = ("name", "value", "text")

# Short literals ("1", "a") match too much to parameterize safely.
MIN_MATCH_LENGTH = 3

# A field carrying a value that was templated from the sample row.
ROW_TEMPLATE_MARKER = "{{row."

# Rung fields whose value is the rung's own description of the element it names.
_LABEL_FIELDS = ("name", "text", "value", "selector")


def _cells(sample_row: dict[str, Any]) -> list[tuple[str, str]]:
    """The sample row's non-empty cells, as (column, text), longest text first."""
    cells = [
        (column, str(value).strip())
        for column, value in sample_row.items()
        if value is not None and str(value).strip()
    ]
    cells.sort(key=lambda cell: len(cell[1]), reverse=True)
    return cells


def match_column(
    value: Any, sample_row: dict[str, Any], *, prefer: tuple[str, ...] = ()
) -> str | None:
    """The input column whose sample value equals this literal, if any.

    PL-078: two fields can be taught the same value -- a timesheet's Monday and Tuesday are both
    ``2:00`` in the demonstration -- so the value alone does not say which column a fill belongs
    to. ``prefer`` carries the step's own field names (its step name and its target rungs,
    squashed); when more than one column matches the value, the one whose name is part of this
    step's own name wins. A step that names no matching field keeps the first match, as before.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    matches = [column for column, cell in _cells(sample_row) if cell.casefold() == text.casefold()]
    if len(matches) > 1 and prefer:
        owned = [
            column
            for column in matches
            if _column_token(column) and any(_column_token(column) in token for token in prefer)
        ]
        if len(owned) == 1:
            return owned[0]
    return matches[0] if matches else None


def _squash(text: str) -> str:
    """Lowercase letters and digits only: `Fill 20Tue` and `f_20tue` become `fill20tue`, `20tue`."""
    return "".join(char for char in text.lower() if char.isalnum())


def _column_token(column: str) -> str:
    """A column's name without the popup's `f_` prefix, squashed for comparison."""
    base = column[2:] if column.startswith("f_") else column
    return _squash(base)


def _field_tokens(candidate: Candidate) -> tuple[str, ...]:
    """The names this step's own field carries, squashed: its step name and its target rungs."""
    texts = [candidate.name or ""]
    for rung in candidate.targets or []:
        for key in ("name", "text", "value"):
            text = rung.get(key)
            if isinstance(text, str):
                texts.append(text)
    return tuple(_squash(text) for text in texts if _squash(text))


def parameterize_value(
    value: Any, sample_row: dict[str, Any], *, prefer: tuple[str, ...] = ()
) -> tuple[Any, str | None]:
    """Replace a literal value with a reference to the input column it came from."""
    if not isinstance(value, dict) or value.get("kind") != "constant":
        return value, None
    column = match_column(value.get("value"), sample_row, prefer=prefer)
    if column is None:
        return value, None
    return {"kind": "input", "column": column}, f"bound a literal value to column {column!r}"


# PL-114: the words a sheet cell may use for a checkbox. A real boolean means itself.
YES_WORDS = frozenset({"yes", "y", "true", "1", "x", "\u2713", "\u2714", "on"})
NO_WORDS = frozenset({"no", "n", "false", "0", "", "off"})


def yes_no(value: Any) -> bool | None:
    """The yes/no a cell means, or None when it says neither."""
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text in YES_WORDS:
        return True
    if text in NO_WORDS:
        return False
    return None


def bind_check_column(taught: bool, sample_row: dict[str, Any]) -> tuple[Any, list[str]]:
    """Bind a taught check to the one column whose cell means the same yes/no.

    With no such column the taught value stands and every row gets it, as it did before. With
    several, the step is left as taught and the warning says why: a wrong bound column would tick
    the box for the wrong rows, which is worse than repeating the demonstration.
    """
    columns = [column for column, cell in sample_row.items() if yes_no(cell) == taught]
    if len(columns) == 1:
        return (
            {"kind": "input", "column": columns[0]},
            [f'the "check" step\'s yes/no is bound to the {columns[0]} column'],
        )
    if columns:
        return (
            {"kind": "constant", "value": taught},
            ['more than one column looks like a yes/no for the "check" step; it is left as taught'],
        )
    return {"kind": "constant", "value": taught}, []


def parameterize_text(text: str, sample_row: dict[str, Any]) -> tuple[str, str | None]:
    """Replace recorded literals embedded in a rung's text with templates.

    PL-123: the comparison is trimmed and case-insensitive. A person may type "oak standing desk "
    into a search box and the page shows "Oak Standing Desk" on the result; the result is still the
    row's value and becomes ``{{row.product}}``. A name the page glued together -- Odoo's kanban
    card reads "demo dayyyyyyyMy Company" -- keeps only the row value, never the glued text.
    """
    result = text
    replaced: set[str] = set()
    for column, cell in _cells(sample_row):
        if len(cell) < MIN_MATCH_LENGTH:
            continue
        match = re.search(re.escape(cell), result, re.IGNORECASE)
        if match is None:
            continue
        start, end = match.span()
        template = f"{{{{row.{column}}}}}"
        glued_before = start > 0 and not result[start - 1].isspace()
        glued_after = end < len(result) and not result[end].isspace()
        if (start == 0 and glued_after) or (end == len(result) and glued_before):
            # The literal is one run of a name the page glued together. The row's value is the
            # part that identifies the element; the rest is the page's own layout text.
            result = template
        else:
            result = result[:start] + template + result[end:]
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


def _rung_names_row(rung: dict[str, Any]) -> bool:
    """Whether any of a rung's text fields carries a template of the sample row."""
    return any(
        isinstance(rung.get(field_name), str) and ROW_TEMPLATE_MARKER in rung[field_name]
        for field_name in ("name", "text", "value")
    )


def _rung_label(rung: dict[str, Any]) -> str:
    """A rung named the way the recipe reads it: `css "#user_id_0_0_0"`."""
    by = str(rung.get("by", ""))
    for field_name in _LABEL_FIELDS:
        text = rung.get(field_name)
        if isinstance(text, str) and text.strip():
            return f'{by} "{text.strip()}"'
    return by


def _is_option_click(candidate: Candidate) -> bool:
    """Whether a click's ladder names a suggestion option (as ``compiler._is_option_click``)."""
    return candidate.action == "click" and any(
        str(rung.get("role", "")).lower() == "option" for rung in candidate.targets or []
    )


def _drop_fixed_option_rungs(
    targets: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep only the rungs of an option click that name the row's value (PL-096).

    Once the option rung is templated from the row -- ``role option
    "{{row.salesperson}}"`` -- the ladder's fixed rungs, a css path or a test id, still
    point at the option that was recorded. A row naming a value the list does not have
    misses the role rung, and the fixed rung then clicks whichever option sits there: the
    first one, saving the record with someone else's value. Drop them. An option click
    with nothing templated ("Search More...") is a fixed choice and keeps its ladder.
    """
    if not any(_rung_names_row(rung) for rung in targets):
        return targets, []
    kept = [rung for rung in targets if _rung_names_row(rung)]
    warnings = [
        f"dropped rung {_rung_label(rung)}: it points at the recorded option, not the row's"
        for rung in targets
        if not _rung_names_row(rung)
    ]
    return kept, warnings


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

    if _is_option_click(candidate):
        targets, dropped_warnings = _drop_fixed_option_rungs(targets)
        warnings.extend(dropped_warnings)

    value = candidate.value
    if value is not None:
        if (
            candidate.action == "check"
            and isinstance(value, dict)
            and value.get("kind") == "constant"
            and isinstance(value.get("value"), bool)
        ):
            value, check_warnings = bind_check_column(value["value"], sample_row)
            warnings.extend(check_warnings)
        else:
            value, note = parameterize_value(value, sample_row, prefer=_field_tokens(candidate))
            if note:
                warnings.append(note)

    return replace(candidate, targets=targets, value=value), warnings
