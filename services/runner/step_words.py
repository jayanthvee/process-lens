"""Plain words for a step that did not finish (PL-097).

A record that stops on a step used to show the extension's own diagnosis as its reason:

    the click target could not be resolved: the target is not clickable: the box center is
    covered by <div>

That sentence is accurate and useless to the person reading the run page. This module turns the
step, the row and the raw error into one sentence they can act on, in the same style as
:mod:`services.runner.check_words` (PL-047). It is pure: no database, no browser.

The raw error is never lost. The run loop keeps it on the step event, where whoever debugs the run
can still read it; only the record's ``reason`` carries the plain sentence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .check_words import MAX_SENTENCE
from .templates import TemplateError, render

# PL-097: the extension's phrases, matched case-insensitively. They are not an enum -- the
# extension composes them from a locator's own error -- so each phrase is a signal, not a grammar.
_EMPTY_FILL = "resolved to an empty value"
_DID_NOT_STICK = "did not stick"
_NOT_CLICKABLE = ("not clickable", "covered by", "is covered")
_NOT_FOUND = ("no rung matched", "could not be resolved", "not found")

# The filters a template may carry after its column, as `templates` documents them.
_ROW_COLUMN = re.compile(r"\{\{\s*row\.(?P<column>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\|[^{}]*)?\}\}")


def describe_step_failure(
    step: Mapping[str, Any],
    row: Mapping[str, Any],
    variables: Mapping[str, Any],
    error: str | None,
) -> str:
    """One plain sentence for a step that did not finish.

    The sentence follows the situation, most specific first:

    * a fill whose value came out empty names the column (``No salesperson in this row``);
    * a fill the page changed as it was typed says so, and names the column;
    * a click on an option templated from the row says that value is not in the list;
    * any other click that something covered says what could not be clicked;
    * any other target that was not on the page names the element;
    * anything else keeps the raw error, clipped to :data:`MAX_SENTENCE`, so it stays readable.

    An option click cannot see the fill that typed into the list -- the recipe's earlier steps are
    not passed here -- so the field's name comes from the row column in the option's own template.
    """
    action = step.get("action")
    text = "" if error is None else str(error)
    lowered = text.lower()

    if not text:
        return _fit(f"Step “{_step_name(step)}” didn't finish")

    if action == "fill":
        if _EMPTY_FILL in lowered:
            return _fit(f"No {_column_phrase(step)} in this row")
        if _DID_NOT_STICK in lowered:
            column = _column_phrase(step)
            return _fit(f"The page changed the {column} as it was typed; check the row's {column}")

    if action == "click":
        option = _option(step, row, variables)
        if option is not None and _is_locator_failure(lowered):
            value, column = option
            # PL-099: `product_2` is the recorder's second column for one field, not a field the
            # person knows; the list is the Product list.
            field = re.sub(r"_\d+$", "", column) or column
            return _with_value("“", value, f"” isn't in the {_label(field)} list")
        name = _element_name(step, row, variables)
        if _mentions(lowered, _NOT_CLICKABLE):
            return _with_value("Couldn't click “", name, "”: something was in front of it")
        if _mentions(lowered, _NOT_FOUND):
            return _with_value("Couldn't find “", name, "” on the page")

    return _fit(f"Step “{_step_name(step)}” didn't finish: {text}")


def _is_locator_failure(lowered: str) -> bool:
    """The failure modes an option click can have: it was not found, or it was covered."""
    return _mentions(lowered, _NOT_FOUND) or _mentions(lowered, _NOT_CLICKABLE)


def _mentions(lowered: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in lowered for phrase in phrases)


def _option(
    step: Mapping[str, Any], row: Mapping[str, Any], variables: Mapping[str, Any]
) -> tuple[str, str] | None:
    """The value and the row column of the option rung, when the click names one from the row.

    A click on an option is how a recipe picks a value from a list: the rung's ``name`` is the
    template the list entry must match. A literal name says nothing about a field, so it is left to
    the click sentences.
    """
    targets = step.get("targets")
    if not isinstance(targets, list):
        return None
    for target in targets:
        if not isinstance(target, Mapping):
            continue
        if target.get("by") != "role" or target.get("role") != "option":
            continue
        name = target.get("name")
        if not isinstance(name, str):
            return None
        column = _row_column(name)
        if column is None:
            return None
        return _text(name, row, variables), column
    return None


def _row_column(template: str) -> str | None:
    """The first ``row.<column>`` a template reads, if it reads one."""
    match = _ROW_COLUMN.search(template)
    return match.group("column") if match else None


def _element_name(
    step: Mapping[str, Any], row: Mapping[str, Any], variables: Mapping[str, Any]
) -> str:
    """What to call the element a step tried to use.

    The first ``role`` rung's name is what the page calls it (``Save manually``); when the rung
    carries no name, the step's own name without its leading ``Click `` / ``Fill `` is the best a
    person can get.
    """
    targets = step.get("targets")
    if isinstance(targets, list):
        for target in targets:
            if not isinstance(target, Mapping) or target.get("by") != "role":
                continue
            name = target.get("name")
            if isinstance(name, str) and name.strip():
                return _text(name, row, variables)
            break
    return _strip_action(_step_name(step))


def _strip_action(name: str) -> str:
    for prefix in ("Click ", "Fill "):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _step_name(step: Mapping[str, Any]) -> str:
    name = step.get("name")
    if isinstance(name, str) and name.strip():
        return name
    step_id = step.get("id")
    if isinstance(step_id, str) and step_id.strip():
        return step_id
    return "this step"


def _column_phrase(step: Mapping[str, Any]) -> str:
    """A fill's column, lower-cased for the middle of a sentence (``salesperson``)."""
    value = step.get("value")
    column = value.get("column") if isinstance(value, Mapping) else None
    label = _label(column) if isinstance(column, str) and column else "value"
    return label.lower()


def _label(column: str) -> str:
    """A column's name in sentence case: ``contact_name`` -> ``Contact name``.

    The same spelling as the batch summary's ``batches._label``, so one column is called the same
    thing on both pages.
    """
    return " ".join(str(column).replace("_", " ").split()).capitalize()


def _text(value: Any, row: Mapping[str, Any], variables: Mapping[str, Any]) -> str:
    """Render a template with the runner's own renderer; an unrenderable one stays literal."""
    if not isinstance(value, str):
        return str(value)
    try:
        return render(value, row=row, variables=variables)
    except TemplateError:
        return value


def _clip(text: str, limit: int) -> str:
    """At most ``limit`` characters, with ``…`` marking what was cut."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _fit(text: str) -> str:
    """Keep one sentence to the length a record's reason may be read at."""
    return _clip(text, MAX_SENTENCE)


def _with_value(prefix: str, value: str, suffix: str) -> str:
    """Build ``prefix value suffix``, cutting the value so the finding survives.

    The same shape as ``check_words._with_value``: a row can hold a value far longer than a
    reason may be, and truncating the whole sentence would take away the part that says what went
    wrong. The prefix and suffix are kept whole; only the row's own value is cut.
    """
    sentence = f"{prefix}{value}{suffix}"
    if len(sentence) <= MAX_SENTENCE:
        return sentence
    room = MAX_SENTENCE - len(prefix) - len(suffix)
    if room <= 1:
        return _fit(sentence)
    return f"{prefix}{_clip(value, room)}{suffix}"


__all__ = ["describe_step_failure"]
