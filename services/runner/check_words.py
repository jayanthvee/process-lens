"""Plain words for a commit's failed check (PL-047).

A save is verified by the commit step's ``assert_after``: the page must show some text, an element,
a URL, or a value. When that check does not pass and the record parks, the reconciler's generic
``PARKING_REASON`` says nothing about what the runner actually looked for. This module turns the
condition into one sentence a person can act on.

The sentences are fixed here rather than in the run loop so they can be unit-tested without a
database or a browser. A value longer than :data:`MAX_SENTENCE` characters is truncated with ``…``;
the wait, which is the actionable part, is never cut off.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .conditions import condition_timeout
from .templates import TemplateError, render

# PL-047: one parked reason must stay readable on the dashboard.
MAX_SENTENCE = 160

# PL-047: the schema's condition kinds are closed, but a future kind must still produce a sentence.
UNKNOWN_CHECK = "the save's check did not pass"

# PL-047: a kind the schema allows but this module cannot name gets this neutral phrase.
_GENERIC_ELEMENT = "the element the check looked for"


def describe_check(
    condition: Mapping[str, Any],
    row: Mapping[str, Any],
    variables: Mapping[str, Any],
    *,
    step_name: str | None = None,
) -> str:
    """Turn an ``assert_after`` into one plain sentence, with its templates rendered.

    The schema allows seven condition kinds (``text_visible``, ``text_absent``,
    ``text_count_increases``, ``url_matches``, ``element_present``, ``element_absent``,
    ``value_equals``). A condition that names none of them, or is not an object at all, says only
    :data:`UNKNOWN_CHECK` -- never anything a person could mistake for a real finding.

    ``url_matches`` names the step whose page did not move on, never the pattern: a regex with
    slashes in it is engine detail, and PL-117 item 4 keeps it off every screen.
    """
    if not isinstance(condition, Mapping):
        return UNKNOWN_CHECK

    seconds = _seconds(condition)
    within = f" within {seconds} seconds"

    if "text_visible" in condition:
        text = _text(condition["text_visible"], row, variables)
        return _with_value("the page never showed ", text, within)
    if "text_absent" in condition:
        text = _text(condition["text_absent"], row, variables)
        return _with_value("the page never stopped showing ", text, within)
    if "text_count_increases" in condition:
        text = _text(condition["text_count_increases"], row, variables)
        return _with_value("", text, f" did not appear one more time{within}")
    if "url_matches" in condition:
        name = (step_name or "").strip()
        if name:
            return _fit(f"The page didn't move on after “{name}”{within}")
        return _fit(f"The page didn't move on{within}")
    if "element_present" in condition:
        name = _element_name(condition["element_present"], row, variables)
        if name:
            return _with_value("the ", name, f" element never appeared{within}")
        return _fit(f"{_GENERIC_ELEMENT} never appeared{within}")
    if "element_absent" in condition:
        name = _element_name(condition["element_absent"], row, variables)
        if name:
            return _with_value("the ", name, f" element never disappeared{within}")
        return _fit(f"{_GENERIC_ELEMENT} never disappeared{within}")
    if "value_equals" in condition:
        spec = condition["value_equals"]
        value = (
            _value_source_text(spec.get("value"), row, variables)
            if isinstance(spec, Mapping)
            else None
        )
        if value is not None:
            return _with_value("the field never came to equal ", value, within)
        return _fit(f"the field never came to equal the expected value{within}")

    return UNKNOWN_CHECK


def parking_reason(check_sentence: str, destination: str | None = None) -> str:
    """The full reason a record parks after its save's check failed.

    ``destination`` is the system the run writes to (``RunClaim.destination_key``). When the run
    cannot name it, the reason says ``the destination`` instead of inventing a product name.
    """
    where = (destination or "").strip() or "the destination"
    tail = "if the row is there, Dismiss it, otherwise Try again"
    return f"{check_sentence}; check {where}: {tail}"


def landing_words(pattern: str) -> str | None:
    """The page a ``url_matches`` check was waiting for, in words a person reads (PL-117).

    The compiler writes the landing as a path pattern: ``/home(?:/.*)?$``. Its anchors and its
    regex groups are engine detail, so only the page's own name survives: ``the home page``. A
    pattern with no readable segment names nothing, and the caller keeps its own sentence.
    """
    text = str(pattern or "").strip()
    if not text:
        return None
    # Drop the regex tail that allows anything below the landing, and the anchors around it.
    text = re.sub(r"\(\?:/\.\*\)\?", "", text)
    text = text.strip("^$")
    # A digit run is a record id, not a page name: the segment before it names the page.
    segments = [segment for segment in text.split("/") if segment]
    while segments and re.search(r"\\[dDwW]|\[", segments[-1]):
        segments.pop()
    if not segments:
        return "the site's home page"
    name = re.sub(r"\\(.)", r"\1", segments[-1])
    name = re.sub(r"[\[\](){}?*+^|]", "", name).replace("-", " ").replace("_", " ").strip()
    if not name or name.isdigit():
        return None
    return f"the {name} page"


def _seconds(condition: Mapping[str, Any]) -> str:
    """The wait a person saw, in seconds, without a trailing ``.0``."""
    return f"{condition_timeout(condition) / 1000:g}"


def _text(value: Any, row: Mapping[str, Any], variables: Mapping[str, Any]) -> str:
    """Render a template with the runner's own renderer; an unrenderable one stays literal.

    ``describe_check`` runs only after the check itself was rendered, so a template error here
    means the value never reached a page. Showing the literal is still more useful than raising
    while a record is being parked.
    """
    if not isinstance(value, str):
        return str(value)
    try:
        return render(value, row=row, variables=variables)
    except TemplateError:
        return value


def _value_source_text(
    source: Any, row: Mapping[str, Any], variables: Mapping[str, Any]
) -> str | None:
    """The value a ``value_equals`` check expected, when this module can name it."""
    if not isinstance(source, Mapping):
        return None if source is None else _text(source, row, variables)
    kind = source.get("kind")
    if kind == "constant":
        value = source.get("value")
        return None if value is None else _text(value, row, variables)
    if kind == "input":
        column = source.get("column")
        if isinstance(column, str) and column in row:
            value = row[column]
            return "" if value is None else str(value)
    return None


def _element_name(
    selector: Any, row: Mapping[str, Any], variables: Mapping[str, Any]
) -> str | None:
    """A human name for the element a selector looks for, if the recipe gives one."""
    if not isinstance(selector, Mapping):
        return None
    description = selector.get("description")
    if isinstance(description, str) and description.strip():
        return _text(description, row, variables)
    targets = selector.get("targets")
    if not isinstance(targets, list):
        return None
    for target in targets:
        if not isinstance(target, Mapping):
            continue
        by = target.get("by")
        if by == "role":
            name = target.get("name")
            if isinstance(name, str) and name:
                return _text(name, row, variables)
            role = target.get("role")
            if isinstance(role, str) and role:
                return role
        elif by == "label":
            text = target.get("text")
            if isinstance(text, str) and text:
                return _text(text, row, variables)
        elif by in ("testid", "text_fuzzy"):
            value = target.get("value")
            if isinstance(value, str) and value:
                return _text(value, row, variables)
        elif by == "css":
            selector_text = target.get("selector")
            if isinstance(selector_text, str) and selector_text:
                return selector_text
    return None


def _clip(text: str, limit: int) -> str:
    """At most ``limit`` characters, with ``…`` marking what was cut."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _fit(text: str) -> str:
    """A sentence that carries no value of its own still obeys the cap."""
    return _clip(text, MAX_SENTENCE)


def _with_value(prefix: str, value: str, suffix: str) -> str:
    """Build ``prefix "value" suffix``, truncating the value to keep the sentence readable.

    The prefix and suffix carry the finding and the wait, so they are kept whole; only the value a
    recipe supplied is cut. If even that cannot fit, the whole sentence falls back to one clipped
    line rather than growing past :data:`MAX_SENTENCE`.
    """
    sentence = f'{prefix}"{value}"{suffix}'
    if len(sentence) <= MAX_SENTENCE:
        return sentence
    room = MAX_SENTENCE - len(prefix) - len(suffix) - 2
    if room <= 1:
        return _fit(sentence)
    return f'{prefix}"{_clip(value, room)}"{suffix}'


__all__ = ["MAX_SENTENCE", "UNKNOWN_CHECK", "describe_check", "landing_words", "parking_reason"]
