"""Turn a raw recorded stream into ordered, actionable events.

Two jobs: read the recorder's event envelope without trusting it, and drop the
events that say nothing about the workflow. Focus, blur, scroll, and pointer
movement are noise — a workflow is made of clicks, typing, selections, and
submits, and everything else is removed here rather than carried forward.

The recorder also emits ``observation`` events: what the page showed just after a
click or a submit. They are not actions, so they never become steps of their own;
each is folded onto the action it followed, where it becomes the evidence for
that step's assertion.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from .assertions import OBSERVATION_EVENT_KIND
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


# The actions the recorder watches after: a click or a submit is what can commit.
def _attach_observations(events: list[Event]) -> tuple[list[Event], int, int]:
    """Fold each observation event onto the action it followed.

    An observation is evidence about an action, so it is merged onto the most
    recent click or submit before it and removed from the stream. An observation
    with no such action before it, or one whose action already carries an
    observation, is dropped: the first evidence wins, and a bare observation is
    not a step of its own.
    """
    attachable = {"click", "submit"}
    folded: list[Event] = []
    attached = 0
    orphaned = 0
    last_action: int | None = None

    for event in events:
        if event.kind == OBSERVATION_EVENT_KIND:
            if not event.observation or last_action is None:
                orphaned += 1
                continue
            target = folded[last_action]
            if target.observation is not None:
                orphaned += 1
                continue
            folded[last_action] = replace(target, observation=event.observation)
            attached += 1
            continue
        folded.append(event)
        if event.kind in attachable:
            last_action = len(folded) - 1

    return folded, attached, orphaned


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

        seq = raw.get("seq")
        seq = seq if isinstance(seq, int) else index + 1

        observation = raw.get("observation")
        if observation is not None and not isinstance(observation, Mapping):
            warnings.append(f"event {index} has a malformed observation; ignored")
            observation = None

        if kind == OBSERVATION_EVENT_KIND:
            if observation is None:
                warnings.append(f"event {index} is an observation with no payload; skipped")
                continue
            events.append(
                Event(
                    seq=seq,
                    kind=kind,
                    url=_coerce_text(raw.get("url")),
                    step=None,
                    observation=dict(observation),
                )
            )
            continue

        if kind not in ACTIONABLE_KINDS:
            dropped[kind] = dropped.get(kind, 0) + 1
            continue

        step = raw.get("step")
        if step is not None and not isinstance(step, Mapping):
            warnings.append(f"event {index} has a malformed step; treated as empty")
            step = None

        events.append(
            Event(
                seq=seq,
                kind=kind,
                url=_coerce_text(raw.get("url")),
                step=dict(step) if step is not None else None,
                observation=dict(observation) if observation is not None else None,
            )
        )

    for kind, count in sorted(dropped.items()):
        warnings.append(f"dropped {count} {kind!r} event(s) as noise")

    events.sort(key=lambda event: event.seq)
    events, attached, orphaned = _attach_observations(events)
    if attached:
        warnings.append(f"attached {attached} observation(s) to the action they followed")
    if orphaned:
        warnings.append(f"dropped {orphaned} observation(s) that followed no action")
    return events, warnings
