"""Infer the assertion that proves a commit happened.

A commit step must carry ``assert_after``: a condition on what the page shows
once the destination accepted the change. The compiler can only report what the
recording observed, so it infers in this order:

1. an explicit observation attached to the event (a confirmation appeared, a
   dialog closed, an element appeared, a field's value changed);
2. a navigation the recording observed after the action — the page moved, so
   matching the new path is evidence.

If neither is available the compiler refuses to mark the step as a commit rather
than inventing an assertion. An unverifiable commit is worse than a compiled
recipe that tells the builder to re-record.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

# The condition predicates a recorded observation may carry, from the recipe
# schema. A key outside this set is an observation the compiler does not know.
OBSERVATION_KEYS = (
    "text_visible",
    "text_absent",
    "url_matches",
    "element_present",
    "element_absent",
    "value_equals",
)

DEFAULT_ASSERTION_TIMEOUT_MS = 8000


def condition_from_observation(
    observation: dict[str, Any] | None, timeout_ms: int = DEFAULT_ASSERTION_TIMEOUT_MS
) -> dict[str, Any] | None:
    """Build an assertion from what the recording observed, if it observed anything."""
    if not observation:
        return None
    for key in OBSERVATION_KEYS:
        if key in observation:
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


def condition_from_navigation(
    before_url: str, after_url: str, timeout_ms: int = DEFAULT_ASSERTION_TIMEOUT_MS
) -> dict[str, Any] | None:
    """Build an assertion from a navigation the recording observed.

    Only a same-site move counts; crossing to another origin is not something an
    assertion on this page should encode.
    """
    if not before_url or not after_url or before_url == after_url:
        return None
    if origin_of(before_url) != origin_of(after_url):
        return None
    pattern = path_pattern(after_url)
    if not pattern:
        return None
    return {"url_matches": pattern, "timeout_ms": timeout_ms}


def infer_assertion(
    observation: dict[str, Any] | None,
    before_url: str,
    after_url: str | None,
    timeout_ms: int = DEFAULT_ASSERTION_TIMEOUT_MS,
) -> dict[str, Any] | None:
    """The assertion for a commit step, from observation first, then navigation."""
    from_observation = condition_from_observation(observation, timeout_ms)
    if from_observation is not None:
        return from_observation
    if after_url:
        return condition_from_navigation(before_url, after_url, timeout_ms)
    return None
