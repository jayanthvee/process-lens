"""PL-125: read what a task is from a recording, instead of replaying the recording.

A person's demonstration is a handful of meaningful actions inside a lot of noise: values the page
filled in by itself, a stray menu click, a typo fixed, a click on a stock button, even another
tab's activity. Earlier compilers replayed the recording and subtracted the noise one rule at a
time. This module goes the other way. The owner's tasks have one of two shapes, and each shape
needs only a few facts, which the recording answers almost mechanically:

* **update** -- go to the list, open the item a row names, change some fields, save;
* **create** -- go to the list, press New, fill some fields, save.

=================  ===========================================================================
The shape needs   Where it comes from
=================  ===========================================================================
the list           the page the item was opened from
the item           the click after which the address became one item's page (``.../17``), or
                   a new item's page (``.../new``); the item's name is the value the person
                   searched for, else the page's own name field found in the clicked text
the changes        what the person typed on that item's page -- never what the page set by
                   itself -- the last value per field
the save           a Save click, or leaving the page after the last change
=================  ===========================================================================

Nothing else in the recording is used, so nothing has to be removed. Everything here is decided
from the recording's own events and addresses, with no model and no site's words.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

_UUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
_SAVE_WORD = re.compile(r"(?<![a-z0-9])save(?![a-z0-9])", re.IGNORECASE)
_CHANGE_ACTIONS = ("fill", "select", "check")
_BRACKETED = re.compile(r"\s*[\[(][^\])]*[\])]\s*")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

NO_ITEM = (
    "ProcessLens couldn't see which item you opened. Open the item from its list, change it, "
    "then Stop."
)
NO_CHANGE = (
    "This task doesn't change anything. Change the value you want each row to set, then Stop again."
)
NO_SAVE = (
    "ProcessLens didn't see the change saved. Press Save, or leave the page, before you press Stop."
)
UNKNOWN_ITEM = (
    "ProcessLens couldn't tell which item you opened. Search for it by name, open it, then Stop."
)


@dataclass
class Change:
    """One field the person set on the item's page, with the value they left in it."""

    event: dict[str, Any]
    label: str
    column: str
    value: Any
    was: Any = None


@dataclass
class Shape:
    """What the task is: its kind and the few recorded events that carry it."""

    kind: str | None = None  # "update", "create", or None when the recording fits neither
    problem: str | None = None
    origin: str = ""
    list_url: str = ""
    item_text: str = ""
    item_column: str = ""
    item_label: str = ""
    search: list[dict[str, Any]] = field(default_factory=list)
    open_event: dict[str, Any] | None = None
    changes: list[Change] = field(default_factory=list)
    save: str | None = None  # "button" or "leave"
    save_event: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        """The plain facts the panel asks its three questions from."""
        return {
            "kind": self.kind,
            "problem": self.problem,
            # An item was opened, so the recording is an update or a create even when it can't be
            # saved yet; the panel then says what is missing instead of offering the step list.
            "opened": self.kind is not None or self.problem in (NO_CHANGE, NO_SAVE, UNKNOWN_ITEM),
            "item": {"text": self.item_text, "label": self.item_label, "column": self.item_column}
            if self.kind
            else None,
            "changes": [
                {
                    "label": change.label,
                    "column": change.column,
                    "value": change.value,
                    "was": change.was,
                }
                for change in self.changes
            ],
            "save": self.save,
            "save_label": _event_name(self.save_event) if self.save_event else None,
        }


def _origin(url: str) -> str:
    parts = urlsplit(url or "")
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def _path(url: str) -> str:
    return urlsplit(url or "").path.rstrip("/")


def _tail(url: str) -> str:
    return _path(url).rsplit("/", 1)[-1]


def _is_item_page(url: str) -> bool:
    tail = _tail(url)
    return tail.isdigit() or _UUID.fullmatch(tail) is not None


def _is_new_page(url: str) -> bool:
    return _tail(url).lower() == "new"


def _step(event: dict[str, Any]) -> dict[str, Any]:
    step = event.get("step")
    return step if isinstance(step, dict) else {}


def _action(event: dict[str, Any]) -> str:
    return str(_step(event).get("action") or event.get("kind") or "")


def _by_person(event: dict[str, Any]) -> bool:
    return event.get("page_set") is not True


def _literal(event: dict[str, Any]) -> Any:
    value = _step(event).get("value")
    if isinstance(value, dict) and value.get("kind") == "constant":
        return value.get("value")
    if _action(event) == "check":
        return _step(event).get("checked", value)
    return value


def _event_name(event: dict[str, Any] | None) -> str:
    if not event:
        return ""
    step = _step(event)
    name = step.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    for rung in step.get("targets") or []:
        for key in ("name", "text", "value"):
            text = rung.get(key) if isinstance(rung, dict) else None
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _target_text(event: dict[str, Any]) -> str:
    """Every word the click's own target ladder carries, for finding the item's name in it."""
    parts = [_event_name(event)]
    for rung in _step(event).get("targets") or []:
        if not isinstance(rung, dict):
            continue
        for key in ("name", "text", "value"):
            text = rung.get(key)
            if isinstance(text, str):
                parts.append(text)
    return " ".join(parts)


def _field_label(event: dict[str, Any]) -> str:
    """The field's own words: "Sales Price" from "Fill Sales Price", else its label rung."""
    name = _event_name(event)
    for verb in ("Fill ", "Select ", "Check ", "Uncheck ", "Set "):
        if name.startswith(verb):
            return name[len(verb) :].strip()
    for rung in _step(event).get("targets") or []:
        if isinstance(rung, dict) and rung.get("by") in ("label", "role"):
            text = rung.get("text") or rung.get("name")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return name or "value"


def _column(label: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "value"
    if base[0].isdigit():
        base = f"c_{base}"
    column, number = base, 2
    while column in taken:
        column, number = f"{base}_{number}", number + 1
    taken.add(column)
    return column


def _field_key(event: dict[str, Any]) -> str:
    targets = _step(event).get("targets") or []
    return json.dumps(targets[:1], sort_keys=True) + "|" + _field_label(event).lower()


def _next_url(events: list[dict[str, Any]], index: int) -> str:
    """The address the page showed next after this event, if it moved."""
    here = _path(events[index].get("url") or "")
    for later in events[index + 1 :]:
        url = later.get("url") or ""
        if url and _path(url) != here:
            return url
    return ""


def read_shape(recording: list[dict[str, Any]]) -> Shape:
    """Read the task's shape from a recording; a recording that fits no shape says why."""
    events = [event for event in recording if isinstance(event, dict)]
    origin = next((_origin(event.get("url") or "") for event in events if event.get("url")), "")
    # Teaching follows one site: an event on another site is another tab's activity, never part
    # of this task (the owner's Odoo recording carried a Goodlabs run's sign-in steps).
    events = [event for event in events if _origin(event.get("url") or "") == origin]
    shape = Shape(origin=origin)

    # The item's page: the last click after which the address became an item's page or a new
    # item's page, and on which the person then changed something.
    opened: tuple[int, str] | None = None
    landed_on_item = False
    for index, event in enumerate(events):
        if _action(event) != "click":
            continue
        landed = _next_url(events, index)
        if not landed or not (_is_item_page(landed) or _is_new_page(landed)):
            continue
        landed_on_item = True
        if any(
            _action(later) in _CHANGE_ACTIONS
            and _by_person(later)
            and _path(later.get("url") or "") == _path(landed)
            for later in events[index + 1 :]
        ):
            opened = (index, landed)
    if opened is None:
        shape.problem = NO_CHANGE if landed_on_item else NO_ITEM
        return shape
    open_index, item_url = opened
    shape.kind = "create" if _is_new_page(item_url) else "update"
    shape.open_event = events[open_index]
    shape.list_url = events[open_index].get("url") or ""
    item_path = _path(item_url)

    # The changes: what the person set on the item's page, the last value per field. A value the
    # page set by itself is never a change, and a field typed twice (a typo fixed) is one change.
    taken: set[str] = set()
    by_field: dict[str, Change] = {}
    page_values: dict[str, Any] = {}
    last_change = open_index
    for index in range(open_index + 1, len(events)):
        event = events[index]
        if _path(event.get("url") or "") != item_path or _action(event) not in _CHANGE_ACTIONS:
            continue
        key = _field_key(event)
        if not _by_person(event):
            page_values.setdefault(key, _literal(event))
            continue
        last_change = index
        if key in by_field:
            by_field[key].event = event
            by_field[key].value = _literal(event)
            continue
        label = _field_label(event)
        by_field[key] = Change(
            event=event,
            label=label,
            column="",
            value=_literal(event),
            was=page_values.get(key),
        )
    changes = list(by_field.values())
    if not changes:
        shape.kind, shape.problem = None, NO_CHANGE
        return shape

    # The item's name, for an update: the value searched for on the list, else the page's own
    # value that the clicked text carries ("Oak Standing Desk" inside "Oak Standing Desk
    # [PL-DESK-1001]"), else the clicked text without its bracketed reference.
    if shape.kind == "update":
        search = [
            event
            for event in events[:open_index]
            if _action(event) == "fill"
            and _by_person(event)
            and _path(event.get("url") or "") == _path(shape.list_url)
            and isinstance(_literal(event), str)
            and _literal(event).strip()
        ]
        clicked = _target_text(shape.open_event).lower()
        page_text = [
            (str(value).strip(), key.rsplit("|", 1)[-1])
            for key, value in page_values.items()
            if isinstance(value, str) and len(value.strip()) >= 3
        ]
        page_named = [(text, label) for text, label in page_text if text.lower() in clicked]
        if search:
            last = search[-1]
            shape.item_text = str(_literal(last)).strip()
            shape.search = [last]
            run = events.index(last) + 1
            if run < open_index and _action(events[run]) == "press":
                shape.search.append(events[run])
            matching = [
                label for text, label in page_text if text.lower() == shape.item_text.lower()
            ]
            shape.item_label = matching[0] if matching else ""
        elif page_named:
            text, label = max(page_named, key=lambda named: len(named[0]))
            shape.item_text, shape.item_label = text, label
        else:
            shape.item_text = _BRACKETED.sub(" ", _event_name(shape.open_event)).strip()
            if shape.item_text.lower().startswith("click "):
                shape.item_text = shape.item_text[6:].strip()
        if len(shape.item_text) < 3 or shape.item_text.isdigit():
            shape.kind, shape.problem = None, UNKNOWN_ITEM
            return shape
        shape.item_label = (shape.item_label or "item").strip()
        shape.item_column = _column(shape.item_label, taken)

    for change in changes:
        change.column = _column(change.label, taken)
    shape.changes = changes

    # The save: a Save click after the last change, else any later click or a move off the page.
    after = events[last_change + 1 :]
    button = next(
        (
            event
            for event in after
            if _action(event) == "click" and _SAVE_WORD.search(_target_text(event))
        ),
        None,
    )
    if button is not None:
        shape.save, shape.save_event = "button", button
    else:
        # The leave is the person's last action on the item's page: a stray click before it (a
        # stock button, a tab) is not how they left, and a move off the page ends the search.
        leave: dict[str, Any] | None = None
        for event in after:
            if event.get("url") and _path(event.get("url") or "") != item_path:
                leave = leave or event
                break
            if _action(event) == "click":
                leave = event
        if leave is None:
            shape.kind, shape.problem = None, NO_SAVE
            return shape
        shape.save = "leave"
        shape.save_event = leave if _action(leave) == "click" else None

    if shape.kind == "create":
        emails = [change for change in changes if _EMAIL.match(str(change.value or "").strip())]
        key = (emails or changes)[0]
        shape.item_column, shape.item_label = key.column, key.label
    return shape


def canonical_recording(shape: Shape) -> list[dict[str, Any]]:
    """The recording the compiler builds the task from: the shape's events and nothing else.

    The open-click is aimed at the item's name, so the compiler turns it into the row's column;
    leaving the page ends on the list's address, which is where the person went back to.
    """
    assert shape.kind is not None and shape.open_event is not None
    out: list[dict[str, Any]] = []

    def add(event: dict[str, Any]) -> None:
        copy = json.loads(json.dumps(event))
        copy["seq"] = len(out) + 1
        out.append(copy)

    for event in shape.search:
        add(event)
    add(shape.open_event)
    if shape.kind == "update":
        step = out[-1]["step"]
        role = next(
            (
                rung.get("role")
                for rung in step.get("targets") or []
                if isinstance(rung, dict) and rung.get("by") == "role" and rung.get("role")
            ),
            "generic",
        )
        step["targets"] = [
            {"by": "role", "role": role, "name": shape.item_text},
            {"by": "text_fuzzy", "value": shape.item_text},
        ]
        step["name"] = f"Open {shape.item_text}"[:120]
    for change in shape.changes:
        add(change.event)
    if shape.save == "button" and shape.save_event is not None:
        add(shape.save_event)
    else:
        if shape.save_event is not None:
            add(shape.save_event)
        out.append({"seq": len(out) + 1, "kind": "navigate", "url": shape.list_url, "step": None})
    return out


def sample_row(shape: Shape) -> dict[str, Any]:
    """The taught values, one per column: the item's name and each change's value."""
    row: dict[str, Any] = {}
    if shape.kind == "update":
        row[shape.item_column] = shape.item_text
    for change in shape.changes:
        row[change.column] = "" if change.value is None else change.value
    return row
