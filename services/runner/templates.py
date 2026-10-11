"""Template rendering for step values and keys.

``{{row.<column>}}`` reads an input column; ``{{<variable>}}`` reads a variable. The filters
``lower``, ``upper`` and ``trim`` may follow, separated by ``|``. An unknown filter, a missing
column, or a missing variable is an error: a value is never silently rendered as an empty string.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

_FILTERS: dict[str, Callable[[str], str]] = {
    "lower": lambda value: value.lower(),
    "upper": lambda value: value.upper(),
    "trim": lambda value: value.strip(),
}

_PLACEHOLDER = re.compile(r"\{\{\s*(?P<body>[^{}]*?)\s*\}\}")


class TemplateError(ValueError):
    """A template could not be rendered. The message names the placeholder and the cause."""


def _resolve(body: str, row: Mapping[str, Any], variables: Mapping[str, Any]) -> str:
    parts = [part.strip() for part in body.split("|")]
    source = parts[0]
    filters = parts[1:]

    if source.startswith("row."):
        column = source[len("row.") :]
        if column not in row:
            raise TemplateError(f"the input row has no column {column!r}")
        value = row[column]
    else:
        if source not in variables:
            raise TemplateError(f"no variable named {source!r} is bound")
        value = variables[source]

    if value is None:
        raise TemplateError(f"{source!r} has no value")

    text = value if isinstance(value, str) else str(value)
    for name in filters:
        if name not in _FILTERS:
            raise TemplateError(f"unknown template filter {name!r}")
        text = _FILTERS[name](text)
    return text


def render(template: str, *, row: Mapping[str, Any], variables: Mapping[str, Any]) -> str:
    """Render every ``{{ }}`` placeholder in ``template``.

    Raises :class:`TemplateError` on an unknown filter, a missing column, or a missing variable.
    """

    def replace(match: re.Match[str]) -> str:
        return _resolve(match.group("body"), row, variables)

    return _PLACEHOLDER.sub(replace, template)


def render_row(template: str, row: Mapping[str, Any]) -> str:
    """Render a template that only refers to input columns (a key, or a reason)."""
    return render(template, row=row, variables={})


def is_template(value: Any) -> bool:
    """True when a string carries at least one placeholder."""
    return isinstance(value, str) and bool(_PLACEHOLDER.search(value))
