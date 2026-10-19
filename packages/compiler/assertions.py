"""Infer the assertion that proves a commit happened.

A commit step must carry ``assert_after``: a condition on what the page shows
once the destination accepted the change. The compiler can only report what the
recording observed, so it infers in this order:

1. an explicit observation (a confirmation appeared, a dialog closed, an element
   appeared, a field's value changed, the URL moved);
2. a navigation the recording observed after the action — the page moved, so
   matching the new path is evidence.

The recorder reports observation (1) in one of two ways: as an ``observation``
field on the action event, or as its own event of kind ``observation`` just after
the action. ``normalize`` folds the second form onto the action, so by the time
the assertion is inferred both look the same. The predicates an observation may
carry are listed in :data:`OBSERVATION_KEYS` below.

A weak observation -- a loading spinner's own text, or text read from a node that
was busy when it was read (PL-018, §8.2) -- is not evidence at all: it is refused
here, so the step stays an ordinary step or the compile refuses a commit it
cannot verify.

If neither is available the compiler refuses to mark the step as a commit rather
than inventing an assertion. An unverifiable commit is worse than a compiled
recipe that tells the builder to re-record.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

# The condition predicates a recorded observation may carry, from the recipe
# schema. A key outside this set is an observation the compiler does not know.
OBSERVATION_KEYS = (
    "text_visible",
    "text_absent",
    "text_count_increases",
    "url_matches",
    "element_present",
    "element_absent",
    "value_equals",
)

DEFAULT_ASSERTION_TIMEOUT_MS = 8000

# The event kind the recorder uses for a post-action observation. Such an event
# carries no step: it is evidence about the action before it, and the compiler
# folds it onto that action as the assertion for a commit step.
OBSERVATION_EVENT_KIND = "observation"


# PL-018 (§8.2): an observation that only says "something is happening" is not evidence that the
# destination accepted the change. A spinner's own text would otherwise verify every commit.
WEAK_TEXT_PATTERN = re.compile(
    r"^(loading|saving|sending|working|please wait|one moment)\b", re.IGNORECASE
)
# Text that is only an ellipsis (or dots, or whitespace) says nothing either.
ELLIPSIS_ONLY_PATTERN = re.compile(r"^[\s.\u2026]+$")


def _is_weak_text(text: Any) -> bool:
    """Whether a piece of visible text only reports that work is in progress."""
    stripped = text.strip() if isinstance(text, str) else ""
    if not stripped or ELLIPSIS_ONLY_PATTERN.match(stripped):
        return True
    return WEAK_TEXT_PATTERN.match(stripped) is not None


def _is_weak_observation(observation: dict[str, Any]) -> bool:
    """Whether the recording's evidence is a spinner rather than a confirmation."""
    if observation.get("busy") is True:
        return True
    if "text_visible" not in observation:
        return False
    return _is_weak_text(observation.get("text_visible"))


# PL-049: a reference the destination generated for one record. It has letters and digits, at
# least three digits, and may carry "/" or "-" separators: S00001, INV/2026/00012, PO-0042. The
# recording reads it off the page after a save, but the next record gets a different one, so a
# replay can never see it again.
GENERATED_REFERENCE_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=(?:[^0-9]*[0-9]){3})[A-Za-z0-9/-]+$")


def looks_like_generated_reference(text: Any) -> bool:
    """Whether text looks like an identifier the application made up for one record."""
    return isinstance(text, str) and GENERATED_REFERENCE_PATTERN.match(text.strip()) is not None


def _is_generated_reference_not_typed(text: Any, sample_values: Iterable[Any] | None) -> bool:
    """Whether an observation text is a generated reference the person did not type.

    ``sample_values`` are the cells of the demonstration's own row: a reference that is one of
    them was typed by the person, so it is part of the workflow, not the app's per-record answer.
    """
    if not looks_like_generated_reference(text):
        return False
    if sample_values is None:
        return True
    recorded = str(text).strip()
    return not any(value is not None and str(value).strip() == recorded for value in sample_values)


def observation_is_generated_reference(
    observation: dict[str, Any] | None, sample_values: Iterable[Any] | None
) -> bool:
    """Whether an observation's text is a generated reference the person did not type."""
    if not observation:
        return False
    return _is_generated_reference_not_typed(observation.get("text_visible"), sample_values)


def condition_from_observation(
    observation: dict[str, Any] | None,
    timeout_ms: int = DEFAULT_ASSERTION_TIMEOUT_MS,
    *,
    sample_values: Iterable[Any] | None = None,
) -> dict[str, Any] | None:
    """Build an assertion from what the recording observed, if it observed anything.

    A weak observation -- a loading spinner's own text, or text read while the page was busy --
    yields nothing: it is not evidence that the destination accepted the change, and a commit
    built on it would be verified the moment the spinner appeared.

    PL-049: a ``text_visible`` that looks like a reference the app generated (``S00001``) yields
    nothing either, unless the person typed that value in the sample row. It is the
    demonstration's own record number, and every replay would wait for a page that cannot exist.
    """
    if not observation:
        return None
    if _is_weak_observation(observation):
        return None
    for key in OBSERVATION_KEYS:
        if key in observation:
            if key == "text_visible" and _is_generated_reference_not_typed(
                observation[key], sample_values
            ):
                # Another predicate may still be real evidence; only the number is not.
                continue
            return {key: observation[key], "timeout_ms": timeout_ms}
    return None


def origin_of(url: str) -> str:
    """The scheme and host of a URL, or an empty string."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def path_pattern(url: str) -> str | None:
    """A path pattern that identifies the page a navigation landed on.

    The last path segment is dropped when it has a parent, because it often
    carries a record id that differs for every record: ``/records/42`` becomes
    ``/records/``. A single-segment path is kept as it is, because ``/records``
    has no parent to drop back to.

    Note the looseness: ``url_matches`` is matched against the page URL as a
    pattern, so a kept leaf path like ``/records`` also matches ``/records/new``.
    An assertion is preferred from an explicit observation (a confirmation
    message) for exactly this reason; this fallback is used only when the
    recording observed no confirmation at all.
    """
    path = urlsplit(url).path
    if not path:
        return None
    if path.endswith("/"):
        return path
    head, _, _tail = path.rpartition("/")
    return f"{head}/" if head else path


def path_shape_pattern(url: str) -> str | None:
    """A path pattern that keeps the observed page's shape, with digit runs generalised.

    ``/odoo/crm/12`` becomes ``/odoo/crm/\\d+``: the pattern still fits every record, and unlike the
    collapsed :func:`path_pattern` it does *not* fit the page the save was sent from
    (``/odoo/crm/new``). That difference is the point -- an assertion the page before the action
    already satisfies cannot tell a committed change from a refused one.
    """
    path = urlsplit(url).path
    if not path:
        return None
    return re.sub(r"\d+", r"\\d+", re.escape(path))


def condition_from_navigation(
    before_url: str, after_url: str, timeout_ms: int = DEFAULT_ASSERTION_TIMEOUT_MS
) -> dict[str, Any] | None:
    """Build an assertion from a navigation the recording observed.

    Only a same-site move counts; crossing to another origin is not something an
    assertion on this page should encode.

    The navigated page's *shape* is tried first (a record id generalised to ``\\d+``), because a
    shape the step's own page does not match is what makes the assertion evidence. Only when the
    shape cannot tell the two pages apart -- a move between two record ids, say -- does it fall back
    to the collapsed path, which at least names where the action landed.
    """
    if not before_url or not after_url or before_url == after_url:
        return None
    if origin_of(before_url) != origin_of(after_url):
        return None
    shaped = path_shape_pattern(after_url)
    if shaped and not re.search(shaped, before_url):
        return {"url_matches": shaped, "timeout_ms": timeout_ms}
    pattern = path_pattern(after_url)
    if not pattern:
        return None
    return {"url_matches": pattern, "timeout_ms": timeout_ms}


def infer_assertion(
    observation: dict[str, Any] | None,
    before_url: str,
    after_url: str | None,
    timeout_ms: int = DEFAULT_ASSERTION_TIMEOUT_MS,
    *,
    sample_values: Iterable[Any] | None = None,
) -> dict[str, Any] | None:
    """The assertion for a commit step, from observation first, then navigation."""
    from_observation = condition_from_observation(
        observation, timeout_ms, sample_values=sample_values
    )
    if from_observation is not None:
        return from_observation
    if after_url:
        return condition_from_navigation(before_url, after_url, timeout_ms)
    return None
