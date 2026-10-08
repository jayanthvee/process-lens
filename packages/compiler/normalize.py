"""Turn a raw recorded stream into ordered, actionable events.

Two jobs: read the recorder's event envelope without trusting it, and drop the
events that say nothing about the workflow. Focus, blur, scroll, and pointer
movement are noise — a workflow is made of clicks, typing, selections, and
submits, and everything else is removed here rather than carried forward.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .errors import CompileError
from .model import ACTIONABLE_KINDS, Event


def as_event_list(recording: Any) -> list[Any]:
    """Accept a plain list of events, or an object carrying them under ``events``."""
    if isinstance(recording, Mapping):
        events = recording.get("events")
        if events is None:
            raise CompileError("the recording object has no 'events' list")
        recording = events
    if isinstance(recording, (str, bytes)) or not isinstance(recording, Iterable):
        raise CompileError("the recording must be a list of events")
    return list(recording)


def _coerce_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_events(recording: Any) -> tuple[list[Event], list[str]]:
    """Read every event, keep the actionable ones, and report what was dropped.

    A malformed entry is skipped with a note rather than failing the whole
    compile: a recording is captured from a live page, so one odd event should
    not cost the builder the rest of the demonstration.
    """
    events: list[Event] = []
    warnings: list[str] = []
    dropped: dict[str, int] = {}

    for index, raw in enumerate(as_event_list(recording)):
        if not isinstance(raw, Mapping):
            warnings.append(f"event {index} is not an object; skipped")
            continue

        kind = _coerce_text(raw.get("kind")).lower()
        if not kind:
            warnings.append(f"event {index} has no kind; skipped")
            continue
        if kind not in ACTIONABLE_KINDS:
            dropped[kind] = dropped.get(kind, 0) + 1
            continue

        step = raw.get("step")
        if step is not None and not isinstance(step, Mapping):
            warnings.append(f"event {index} has a malformed step; treated as empty")
            step = None

        observation = raw.get("observation")
        if observation is not None and not isinstance(observation, Mapping):
            warnings.append(f"event {index} has a malformed observation; ignored")
            observation = None

        seq = raw.get("seq")
        events.append(
            Event(
                seq=seq if isinstance(seq, int) else index + 1,
                kind=kind,
                url=_coerce_text(raw.get("url")),
                step=dict(step) if step is not None else None,
                observation=dict(observation) if observation is not None else None,
            )
        )

    for kind, count in sorted(dropped.items()):
        warnings.append(f"dropped {count} {kind!r} event(s) as noise")

    events.sort(key=lambda event: event.seq)
    return events, warnings
