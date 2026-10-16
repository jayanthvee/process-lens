"""The working shapes the compiler passes between its stages.

The input mirrors the recorder's event envelope; the intermediate ``Candidate``
is the compiler's own view of a step before it is rendered to the recipe format.
Both are plain data, so every stage is a function from one list to another.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# The event kinds that describe an action. Anything else is noise: focus, blur,
# scroll, mousemove, and the like say nothing about what the workflow does.
# ``press`` is a recorded key; ``unrecordable`` is the recorder's own mark that something
# happened it could not describe as a step, which the compiler must see so it can refuse.
ACTIONABLE_KINDS = frozenset({"click", "fill", "select", "submit", "press", "unrecordable"})

# The step actions the recorder can produce. A submit collapses into a click.
STEP_ACTIONS = ("click", "fill", "select", "press")


@dataclass(frozen=True)
class Event:
    """One recorded interaction, in order."""

    seq: int
    kind: str
    url: str
    step: dict[str, Any] | None
    observation: dict[str, Any] | None = None
    commit: bool = False
    # PL-018 envelope carriers: how the event came about, never what the step does. They ride the
    # envelope because a step may not carry a field the schema does not know.
    chip: bool = False
    autocomplete: bool = False
    supersedes: str | None = None
    # The recorder witnessed that the page itself put this value in the field: the value is real,
    # but the person did not type it, so it is not an action a recipe may replay. It rides the
    # envelope as a boolean so the compiler never has to match the recorder's English sentence.
    page_set: bool = False


@dataclass
class Candidate:
    """A step the compiler is building, before it is rendered."""

    action: str
    targets: list[dict[str, Any]]
    value: Any = None
    name: str | None = None
    key: str | None = None
    url: str = ""
    after_url: str | None = None
    seq: int = 0
    commit: bool = False
    observation: dict[str, Any] | None = None
    assert_after: dict[str, Any] | None = None
    recorded_id: str | None = None
    frame: list[dict[str, Any]] | None = None
    # The envelope carriers, copied from the event so the chip fold can see the relationship.
    chip: bool = False
    autocomplete: bool = False
    supersedes: str | None = None
    page_set: bool = False
    notes: list[str] = field(default_factory=list)


def target_signature(targets: list[dict[str, Any]] | None) -> str:
    """A stable identity for an element's target ladder, for matching two events."""
    return json.dumps(targets or [], sort_keys=True)
