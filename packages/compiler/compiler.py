"""The compile pipeline: recorded events in, a validated recipe out.

The stages run in one order and each is a plain function over the last one's
output:

    parse -> drop noise -> fold keystrokes -> fold press/submit -> fold click/submit
          -> fold chips -> parameterize -> infer assertions -> build meta -> render
          -> validate against the schema

The result is deterministic: the same recording and options always compile to
the same recipe, so a compiled recipe can be diffed and reviewed.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit

from .assertions import (
    DEFAULT_ASSERTION_TIMEOUT_MS,
    NEW_PAGE_RECORD_SEGMENT,
    condition_from_new_page,
    condition_from_observation,
    infer_assertion,
    observation_is_generated_reference,
    origin_of,
    path_pattern,
    sign_in_landing_pattern,
)
from .coalesce import coalesce_fills, collapse_presses, collapse_submits
from .errors import CompileError
from .model import ACTIONABLE_KINDS, STEP_ACTIONS, Candidate, Event, target_signature
from .normalize import NAVIGATE_EVENT_KIND, UNRECORDABLE_EVENT_KIND, parse_events
from .parameterize import match_column, parameterize_candidate
from .validation import assert_valid_recipe

# PL-018 (§3.8, §4.4). Each refusal is one sentence on purpose: it is the headline the Make recipe
# form shows, and nothing is stored when it fires.
KEY_COMMIT_REFUSAL = (
    "The save or send was done with a key. Re-record it by clicking the button (for example Send)."
)
UNRECORDABLE_REFUSAL = (
    "A recipient was removed in a way that can't be replayed (no remove button). Re-record it "
    "using the \u2715, or don't remove recipients during the recording."
)
# The keys a person sends with: a press of one of these may be the send itself.
COMMITTING_KEYS = frozenset({"Enter", "Control+Enter", "Meta+Enter"})

# PL-113 item 1: roles that only move around the page. Their own names are the page's frame, so a
# check that waits for one of them is true whether or not anything was saved.
CHROME_ROLES = frozenset(
    {"tab", "link", "menuitem", "menuitemradio", "menuitemcheckbox", "treeitem"}
)

# PL-113 S2: how long a sign-in wait gives the code email to arrive.
SIGN_IN_WAIT_MS = 90_000

# PL-118 item 1: the sentence a row is left with when the compiler cannot build a read-back. It
# is the same sentence the runner parks a failed read-back with.
READ_BACK_UNKNOWN_REASON = "ProcessLens couldn't confirm the change was saved."
# PL-118 item 2: the keys an app's own save shortcut sends. A press of one of these after the
# change is a leave: it is kept as the step that saves, and the read-back runs afterwards.
SAVE_SHORTCUT_KEYS = frozenset({"Control+Enter", "Meta+Enter"})


@dataclass
class CompileOptions:
    """Everything the compiler needs that the recording does not carry.

    Fields the recipe's ``meta`` block requires come from here, because a
    recording cannot know what the workflow is called or which record key makes
    it safe to re-run.
    """

    name: str
    when_to_use: str
    sample_row: dict[str, Any] = field(default_factory=dict)
    input_fields: dict[str, dict[str, Any]] | None = None
    input_rules: list[dict[str, Any]] | None = None
    record_key: str | None = None
    record_key_column: str | None = None
    idempotency_key: str | None = None
    approval_policy: dict[str, Any] | None = None
    allowed_origins: list[str] | None = None
    commit_step_ids: Iterable[str] | None = None
    commit_assertions: dict[str, dict[str, Any]] | None = None
    start_url: str | None = None
    include_navigate: bool = True
    assertion_timeout_ms: int = DEFAULT_ASSERTION_TIMEOUT_MS
    # PL-017 built-in rules. ``duplicate_check`` proposes the duplicate-search branch (on by
    # default); ``park_fields`` are the columns the person marked required, which become precheck
    # rules that park a record before the browser is touched.
    duplicate_check: bool = True
    park_fields: list[str] | None = None
    # PL-113 S2: this demonstration signed in, and the page it reached after the code page. A
    # sign-in recipe ends by waiting for that page; without it a run that clicked Continue was
    # Done before the code page had even loaded.
    sign_in: bool = False
    sign_in_after_path: str | None = None
    # PL-118: the person chose "The app saves by itself when I leave the page". The commit leaves
    # the form as they did and then re-opens the item, where the changed value is read back.
    save_by_itself: bool = False
    # PL-123: teaching's per-recorded-step intent, keyed by the recorded step id. A step without
    # one gets a plain sentence derived from its own words, so every step carries an intent.
    intents: dict[str, str] | None = None
    # PL-123: the steps `clean_recording` dropped. They are gone before the compiler looks for the
    # leave, so a stray menu click can never be mistaken for it.
    dropped_step_ids: Iterable[str] | None = None


@dataclass
class CompileResult:
    """The compiled recipe and anything the builder should know about it."""

    recipe: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    # PL-077: which of the two searched shapes this recording is, so the Make recipe form can word
    # its rule. ``"update"`` is a find-then-update (the search finds the record to change),
    # ``"create"`` is everything else, and ``None`` means the recording holds no search at all.
    search_kind: str | None = None


def _next_actionable_url(events: list[Event], index: int) -> str | None:
    for following in events[index + 1 :]:
        if following.url:
            return following.url
    return None


def _parse_iso(text: str) -> Any:
    """Parse an ISO timestamp, returning None on failure."""
    from datetime import datetime

    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_page_set_fill(
    event: Event,
    previous_fills: list[tuple[list[dict[str, Any]], Any, str]],
) -> bool:
    """Whether a fill is one the page produced, not the person.

    PL-031: a fill whose value equals the value of a different field filled just before it,
    and that arrived with no keystrokes on its own target, is page_set. When the recorder reports
    a keystroke count, zero keystrokes is decisive. For older recordings without that envelope
    field, the same value on a different field is page_set only when the fill arrived in a burst
    of fills (within 50 ms of another fill), which is how Odoo copies a contact name into the
    Opportunity field.
    """
    if event.page_set:
        return True
    if event.kind != "fill" or not event.step:
        return False
    value = event.step.get("value")
    literal = _literal(value)
    if literal is None or str(literal).strip() == "":
        return False
    current_targets = event.step.get("targets") or []
    current_signature = target_signature(current_targets)
    current_time = _parse_iso(event.at)

    # New recordings: the recorder tells us the value arrived without typing.
    if event.keystrokes == 0:
        for previous_targets, previous_value, _previous_at in previous_fills:
            if target_signature(previous_targets) == current_signature:
                continue
            previous_literal = _literal(previous_value)
            if previous_literal is None:
                continue
            if str(previous_literal).strip() == str(literal).strip():
                return True
        return False

    # Older recordings without keystrokes: infer page-set only from a same-value burst.
    if event.keystrokes is not None:
        return False
    if current_time is None:
        return False

    has_burst = False
    for previous_targets, _previous_value, previous_at in previous_fills:
        if target_signature(previous_targets) == current_signature:
            continue
        previous_time = _parse_iso(previous_at)
        if previous_time is None:
            continue
        if abs((current_time - previous_time).total_seconds()) <= 0.05:
            has_burst = True
            break

    if not has_burst:
        return False

    for previous_targets, previous_value, _previous_at in previous_fills:
        if target_signature(previous_targets) == current_signature:
            continue
        previous_literal = _literal(previous_value)
        if previous_literal is None:
            continue
        if str(previous_literal).strip() == str(literal).strip():
            return True
    return False


def to_candidates(
    events: list[Event], options: CompileOptions
) -> tuple[list[Candidate], list[str]]:
    """Read each actionable event into a candidate step, dropping empty actions.

    PL-112: when the request names the commit steps, that choice is the whole rule. An empty list
    is the person's "Nothing gets saved", so a submit's own ``commit`` (set while folding a
    click/submit pair) is not a commit any more. A caller that predates the field sends ``None``
    and keeps the compiler's earlier behaviour.
    """
    chosen_commits = options.commit_step_ids is not None
    commit_ids = set(options.commit_step_ids or ())
    dropped_ids = set(options.dropped_step_ids or ())
    candidates: list[Candidate] = []
    warnings: list[str] = []
    previous_fills: list[tuple[list[dict[str, Any]], Any, str]] = []

    for index, event in enumerate(events):
        if event.kind == NAVIGATE_EVENT_KIND:
            # A recorded navigation's action is "navigate": it becomes a step at its position.
            if not options.include_navigate:
                continue
            url = event.url
            if not url:
                warnings.append(f"skipped a navigate event at {event.seq}: it carried no url")
                continue
            previous = events[index - 1] if index > 0 else None
            if (
                previous is not None
                and previous.kind not in ("click", "submit")
                and previous.url == url
            ):
                # A reload: the page did not move, so there is nothing to do. A click or a submit
                # before it may have navigated away and back, so a navigate that follows one is a
                # real move, not a reload.
                continue
            candidates.append(
                Candidate(
                    action="navigate",
                    targets=[],
                    url=url,
                    name=f"Open {path_pattern(url) or url}",
                    seq=event.seq,
                )
            )
            continue

        step = event.step or {}
        action = step.get("action") or event.kind
        if action not in STEP_ACTIONS:
            warnings.append(f"skipped event {event.seq} with action {action!r}")
            continue

        # A fill the page itself produced is not something the person demonstrated. It is still a
        # real value on a real page, so it is skipped by name rather than silently dropped: a
        # builder reading the warnings learns that a field filled itself and can re-record with a
        # value they type if the workflow really needs one there.
        if _is_page_set_fill(event, previous_fills):
            warnings.append(
                f"skipped the {action} at event {event.seq}: the page set that value itself, "
                "the demonstration did not type it"
            )
            continue

        targets = step.get("targets")
        if not isinstance(targets, list) or not targets:
            warnings.append(f"skipped {action} event {event.seq}: its target was not captured")
            continue

        value = step.get("value")
        if action == "fill" and isinstance(value, dict) and not value.get("value"):
            warnings.append(f"skipped an empty fill at event {event.seq}")
            continue

        recorded_id = step.get("id") if isinstance(step.get("id"), str) else None
        if recorded_id is not None and recorded_id in dropped_ids:
            # PL-123: teaching already decided this step is the page's own doing or a stray click.
            # It is not a step of the task, and it must never stand in for the leave.
            warnings.append(
                f"dropped the {action} at event {event.seq}: teaching said it was not "
                "part of the task"
            )
            continue
        # PL-112: a step that absorbed another event (a click and its submit) is named by either
        # id, so the person's choice is honoured whichever row they picked.
        named_ids = {recorded_id, *event.also_ids} - {None}
        # PL-114, T1 review B1: a checkbox is never the save. AGENTS.md section 4 lists the steps a
        # commit may be, and a check is not one of them: it sets a field's state. A person who picks
        # its row by mistake is told so, and the compile carries on with the step as an ordinary
        # check rather than producing a recipe the schema would refuse.
        named_as_commit = bool(named_ids & commit_ids)
        if action == "check" and named_as_commit:
            warnings.append(
                "a checkbox can't be the step that saves your work; pick the click that saves it"
            )
        candidates.append(
            Candidate(
                action=action,
                targets=targets,
                value=value,
                name=step.get("name") if isinstance(step.get("name"), str) else None,
                intent=step.get("intent") if isinstance(step.get("intent"), str) else None,
                key=step.get("key") if isinstance(step.get("key"), str) else None,
                url=event.url,
                after_url=_next_actionable_url(events, index),
                seq=event.seq,
                commit=(
                    False
                    if action == "check"
                    else bool(named_ids & commit_ids)
                    if chosen_commits
                    else event.commit or (recorded_id is not None and recorded_id in commit_ids)
                ),
                observation=event.observation,
                recorded_id=recorded_id,
                frame=step.get("frame") if isinstance(step.get("frame"), list) else None,
                checked=step.get("checked") if isinstance(step.get("checked"), bool) else None,
                chip=event.chip,
                autocomplete=event.autocomplete,
                supersedes=event.supersedes,
            )
        )
        if action == "fill":
            previous_fills.append((targets, value, event.at))
    return candidates, warnings


def derive_origins(candidates: list[Candidate], recording_urls: Iterable[str]) -> list[str]:
    """The origins the workflow actually acted on, de-duplicated and ordered."""
    origins = {origin for url in recording_urls if (origin := origin_of(url))}
    origins.update(origin for candidate in candidates if (origin := origin_of(candidate.url)))
    return sorted(origins)


def _derived_field(value: Any) -> dict[str, Any]:
    """One derived input column, with the sample value it was taught with (PL-028).

    The example is what lets the batch check match a spreadsheet column by value; it is cut to the
    schema's 200 characters and left out entirely when the sample held nothing.
    """
    text = "" if value is None else str(value)
    if not text.strip():
        return {"type": "string"}
    return {"type": "string", "example": text[:200]}


def _resolve_meta(
    candidates: list[Candidate],
    recording_urls: list[str],
    options: CompileOptions,
    warnings: list[str],
    *,
    update_columns: list[str] | None = None,
) -> dict[str, Any]:
    origins = list(options.allowed_origins or derive_origins(candidates, recording_urls))
    if not origins:
        raise CompileError(
            "the recording contains no page URLs, so allowed_origins cannot be derived; "
            "pass allowed_origins explicitly"
        )

    fields = options.input_fields
    if not fields:
        if not options.sample_row:
            raise CompileError(
                "an input schema is required: pass input_fields, or a sample_row to derive "
                "string columns from"
            )
        fields = {column: _derived_field(value) for column, value in options.sample_row.items()}
        warnings.append(
            "derived the input schema from the sample row; every column was assumed to be a string"
        )

    record_key = options.record_key
    if not record_key and options.record_key_column:
        record_key = f"{{{{row.{options.record_key_column} | lower}}}}"
    if not record_key:
        raise CompileError(
            "a record_key is required: pass record_key, or record_key_column to build "
            "'{{row.<column> | lower}}' from"
        )

    input_schema: dict[str, Any] = {"fields": fields}
    # PL-017: a column the person marked required parks the record before any step runs. The rule
    # is the schema's precheck shape, so the runner parks with "missing <field>" without a model.
    rules: list[dict[str, Any]] = []
    for column in options.park_fields or []:
        rules.append({"when": {"missing": column}, "then": "park", "reason": f"missing {column}"})
    if options.input_rules:
        rules.extend(options.input_rules)
    if rules:
        input_schema["rules"] = rules

    # PL-079: an update is keyed by the record *and* the values it writes, so a later change to the
    # same record is new work; a create keeps the record key alone. Both halves are the record key's
    # own template, so a custom key keeps its own normalisation.
    idempotency_key = options.idempotency_key or record_key
    if not options.idempotency_key and update_columns:
        values = ":".join(f"{{{{row.{column}}}}}" for column in update_columns)
        idempotency_key = f"{record_key}:{values}"
        warnings.append(
            "the idempotency key carries the values this update writes, so a later change to the "
            "same record is new work"
        )

    return {
        "name": options.name,
        "when_to_use": options.when_to_use,
        "input_schema": input_schema,
        "record_key": record_key,
        "idempotency_key": idempotency_key,
        "approval_policy": options.approval_policy or {"commit": "first_record"},
        "allowed_origins": origins,
    }


def _check_navigate_origins(candidates: list[Candidate], allowed_origins: list[str]) -> None:
    """Refuse a recorded navigation whose origin the caller did not allow.

    ``allowed_origins`` derived from the recording already contains every event url's origin, so
    this fires only when the caller pinned ``allowed_origins`` explicitly.
    """
    allowed = set(allowed_origins)
    problems: list[str] = []
    for candidate in candidates:
        if candidate.action != "navigate":
            continue
        origin = origin_of(candidate.url)
        if origin not in allowed:
            problems.append(
                f"navigate step {candidate.seq or '?'} goes to {candidate.url!r}, "
                f"whose origin {origin or candidate.url!r} is not in allowed_origins"
            )
    if problems:
        raise CompileError("a recorded navigation goes outside allowed_origins", problems=problems)


def _default_name(action: str, targets: list[dict[str, Any]]) -> str:
    for rung in targets:
        for key in ("name", "text", "value"):
            text = rung.get(key)
            if isinstance(text, str) and text:
                return f"{action.capitalize()} {text!r}"
    return f"{action.capitalize()} element"


def _is_back_click(candidate: Candidate) -> bool:
    """Whether this click names a control that goes back (PL-123, T1's second walk item 4).

    The owner taught the narrow window's arrow button, and the wide window draws a breadcrumb link
    instead. The words on the control are the durable part: a name or label saying "back", a CSS
    hook carrying ``back`` in its class, or an intent that says the same. A recipe that is recorded
    narrow therefore still knows this step is the leave when the work window is wide.
    """
    if candidate.action != "click":
        return False
    if candidate.intent and re.search(r"\bback\b", candidate.intent, re.IGNORECASE):
        return True
    for rung in candidate.targets or []:
        for key in ("name", "text", "value", "selector", "within"):
            text = rung.get(key)
            if not isinstance(text, str):
                continue
            # A CSS hook spells it ``o_back_button``; a person reads it as the word "back".
            pattern = r"back" if key in ("selector", "within") else r"\bback\b"
            if re.search(pattern, text, re.IGNORECASE):
                return True
    return False


def _default_intent(candidate: Candidate) -> str:
    """A plain sentence for a step no model named, from the step's own recorded words."""
    if candidate.action == "navigate":
        page = path_pattern(candidate.url) or candidate.url
        return f"open {page}"
    if _is_back_click(candidate):
        return "go back to the previous page"
    label = _field_label(candidate) if candidate.action in ("fill", "select", "check") else None
    if candidate.action == "fill":
        return f"type the {label}" if label else "type the value"
    if candidate.action == "select":
        return f"choose the {label}" if label else "choose the option"
    if candidate.action == "check":
        return f"set the {label}" if label else "set the checkbox"
    if candidate.action == "press":
        return f"press {candidate.key or 'a key'}"
    if candidate.action == "click" and _opens_a_record(candidate):
        # PL-123: a click that opens the item the row names says what it is for, in the words the
        # run-time find needs: "open the product found by {{row.product}}".
        for rung in candidate.targets or []:
            for key in ("name", "text", "value"):
                text = rung.get(key)
                if not isinstance(text, str):
                    continue
                match = re.search(r"\{\{\s*row\.([a-z0-9_]+)", text)
                if match:
                    noun = match.group(1).replace("_", " ")
                    return f"open the {noun} found by {{{{row.{match.group(1)}}}}}"
    name = candidate.name or _default_name(candidate.action, candidate.targets)
    return name[:1].lower() + name[1:] if name else candidate.action


def _intent_for(candidate: Candidate, options: CompileOptions) -> str:
    """The one sentence this step carries: the person's, the recording's, or a derived one."""
    supplied = (options.intents or {}).get(candidate.recorded_id or "")
    if isinstance(supplied, str) and supplied.strip():
        return supplied.strip()[:500]
    if candidate.intent and candidate.intent.strip():
        return candidate.intent.strip()[:500]
    return _default_intent(candidate)[:500]


def _strip_changeable_counts(candidates: list[Candidate]) -> list[str]:
    """Drop a leading count from a control's name, for labels that grow with the data.

    Odoo's smart button reads "0Attendees" while teaching and "1Attendees" after the first
    attendee. The count is the data, not the control's name. The rest of the name stays, so the
    same rung finds the button after the count changes.
    """
    warnings: list[str] = []
    for candidate in candidates:
        if candidate.action != "click":
            continue
        for rung in candidate.targets or []:
            for key in ("name", "text", "value"):
                text = rung.get(key)
                if not isinstance(text, str):
                    continue
                match = re.match(r"^\d[\d,]*\s*([A-Za-z][A-Za-z0-9 _-]{1,})$", text.strip())
                if match is None:
                    continue
                cleaned = match.group(1).strip()
                rung[key] = cleaned
                if candidate.name and candidate.name.lower().startswith("click "):
                    candidate.name = f"Click {cleaned}"[:120]
                warnings.append(
                    f"dropped the changing count from {text!r}: the control is {cleaned!r}"
                )
        # A count-bearing button says what opening it is for; the intent survives a changed count.
        first = (candidate.targets or [{}])[0]
        name = str(first.get("name") or "").strip()
        if name.lower() == "attendees" and not candidate.intent:
            candidate.intent = "open the attendees of the event"
    return warnings


def _drop_tab_clicks(candidates: list[Candidate]) -> tuple[list[Candidate], list[str]]:
    """Drop a click on a page's tab strip: a tab is never a save (PL-123 Events walk item 1).

    Odoo records the Questions tab as ``role=list`` because the click landed on the tab strip
    around it. Its own name is a tab's label, and a click there changes no record, so it is not a
    step of the task. A tab the person needed to reach a field would still be a step in the
    recording before that field; this drops the ones after the form was filled.
    """
    kept: list[Candidate] = []
    warnings: list[str] = []
    seen_fill = False
    for candidate in candidates:
        if candidate.action == "fill":
            seen_fill = True
        if candidate.action == "click" and seen_fill and not candidate.commit:
            roles = {str(rung.get("role", "")).lower() for rung in candidate.targets or []}
            selectors = " ".join(
                str(rung.get("selector", "")) for rung in candidate.targets or []
            ).lower()
            if "tab" in roles or "nav-tabs" in selectors:
                warnings.append(
                    f"dropped the click at event {candidate.seq}: a tab never saves your work"
                )
                continue
        kept.append(candidate)
    return kept, warnings


def _in_place_save_shape(candidates: list[Candidate]) -> dict[str, Any] | None:
    """Find an app that saved a *new* form in place: ``.../new`` became ``.../<id>``.

    PL-123 Events walk: Odoo saved the attendee when its window was hidden, with no step of the
    owner's. A run cannot rely on hiding a window, so the compile makes the save explicit. The
    step before the address move is where the app saved; the fills on the `/new` page are the
    fields a read-back can read after the save.
    """
    new_index: int | None = None
    new_path = ""
    for index, candidate in enumerate(candidates):
        path = urlsplit(candidate.url).path.rstrip("/")
        if path.endswith("/new"):
            new_index = index
            new_path = path
            break
    if new_index is None or not new_path:
        return None
    for index in range(new_index + 1, len(candidates)):
        path = urlsplit(candidates[index].url).path.rstrip("/")
        if path == new_path or not path.startswith(new_path[: -len("/new")] + "/"):
            continue
        tail = path.rsplit("/", 1)[-1]
        if tail.isdigit() or _UUID.fullmatch(tail) is not None:
            fills = [
                position
                for position in range(new_index, index)
                if candidates[position].action == "fill"
                and urlsplit(candidates[position].url).path.rstrip("/") == new_path
                and isinstance(candidates[position].value, dict)
                and candidates[position].value.get("kind") == "input"
            ]
            if not fills:
                return None
            return {
                "last_new": index - 1,
                "record_index": index,
                "new_path": new_path,
                "fills": fills,
            }
    return None


def _new_form_landing(url: str, timeout_ms: int) -> dict[str, Any] | None:
    """The saved page a ``/new`` form must land on, with every id left open.

    PL-059's pattern generalises the form's own last segment. A new attendee sits under an event
    id that differs for every row, so that id is generalised too: the run is proving "this form
    saved", not "it saved under the event the demonstration used".
    """
    if not url or not condition_from_new_page(url, timeout_ms):
        return None
    path = urlsplit(url).path.rstrip("/")
    parts = path.split("/")
    built: list[str] = []
    for part in parts:
        if part == "new":
            built.append(NEW_PAGE_RECORD_SEGMENT)
        elif part.isdigit():
            built.append("[0-9]+")
        else:
            built.append(re.escape(part))
    return {"url_matches": "/".join(built), "timeout_ms": timeout_ms}


def _email_column(fill: Candidate, options: CompileOptions) -> bool:
    column = ""
    if isinstance(fill.value, dict):
        column = str(fill.value.get("column") or "")
    return "email" in column.lower() or (options.record_key_column or "").lower() == column.lower()


def _create_leave_commit(
    candidates: list[Candidate], options: CompileOptions, warnings: list[str]
) -> bool:
    """A new form the app saved when the person left it by the breadcrumb.

    T1's Events walk: leaving the attendee form by "Attendees" saved the new attendee and showed
    the list, and the new row's email is on that list. The leave is the commit, and the list is the
    read-back -- no Save click was recorded.
    """
    new_index = next(
        (
            index
            for index, candidate in enumerate(candidates)
            if urlsplit(candidate.url).path.rstrip("/").endswith("/new")
        ),
        None,
    )
    if new_index is None:
        return False
    new_path = urlsplit(candidates[new_index].url).path.rstrip("/")
    leave_index: int | None = None
    for index in range(new_index + 1, len(candidates)):
        candidate = candidates[index]
        if candidate.action != "click":
            continue
        if _is_back_click(candidate) or (
            candidate.after_url and urlsplit(candidate.after_url).path.rstrip("/") != new_path
        ):
            leave_index = index
    if leave_index is None:
        return False
    leave = candidates[leave_index]
    fills = [
        candidate
        for candidate in candidates[new_index:leave_index]
        if candidate.action == "fill"
        and isinstance(candidate.value, dict)
        and candidate.value.get("kind") == "input"
    ]
    read_fill = next((fill for fill in fills if _email_column(fill, options)), None)
    if read_fill is None or not leave.after_url:
        return False
    column = read_fill.value["column"]
    leave.commit = True
    leave.intent = leave.intent or "go back to the list after saving"
    leave.assert_after = {
        "url_matches": re.escape(urlsplit(leave.after_url).path.rstrip("/")),
        "element_present": {
            "targets": [{"by": "text_fuzzy", "value": f"{{{{row.{column}}}}}"}],
            "intent": "the saved record on the list",
        },
        "timeout_ms": options.assertion_timeout_ms,
    }
    warnings.append(
        "the app saved this new form when it was left; the row is done when the list shows it"
    )
    return True


def _in_place_save_commit(
    candidates: list[Candidate],
    options: CompileOptions,
    warnings: list[str],
    shape: dict[str, Any] | None = None,
) -> bool:
    """Make a create's hidden-window save explicit, with a landing and a value read-back."""
    shape = shape or _in_place_save_shape(candidates)
    if shape is None:
        return False
    fills: list[int] = shape["fills"]
    read_fill = next(
        (candidates[index] for index in fills if _email_column(candidates[index], options)),
        candidates[fills[-1]],
    )
    read_back = _read_back_assertion(read_fill, options)
    landing = _new_form_landing(candidates[shape["last_new"]].url, options.assertion_timeout_ms)
    if read_back is None or landing is None:
        warnings.append(
            "the app saved this new form in place, but the saved page cannot be read back; "
            "every row will be left for you"
        )
        return False
    assertion = {
        "url_matches": landing["url_matches"],
        "value_equals": read_back["value_equals"],
        "timeout_ms": options.assertion_timeout_ms,
    }
    # The save is explicit: the form's own control, found by its intent when the name differs.
    save = Candidate(
        action="click",
        targets=[
            {"by": "role", "role": "button", "name": "Save manually"},
            {"by": "text_fuzzy", "value": "Save"},
        ],
        name="Save the record",
        intent="save the record",
        url=candidates[shape["last_new"]].url,
        commit=True,
        assert_after=assertion,
    )
    candidates.insert(shape["last_new"] + 1, save)
    warnings.append(
        "the app saved this new form in place when its window was hidden; the save is an "
        "explicit step so a run does not depend on a hidden window"
    )
    warnings.append("this save is checked by reading the email back from the saved page")
    return True


def _duplicate_search_bodies(
    candidates: list[Candidate], options: CompileOptions, warnings: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Recipe bodies that look for the row's email before the New click.

    A person may already have registered the same address by hand; the ledger's never-add-twice
    key only knows rows ProcessLens finished. The search makes the destination itself answer, and
    a hit ends the row as already there (skipped), never as a second attendee.
    """
    if not options.duplicate_check or not any(
        _email_column(candidate, options) for candidate in candidates if candidate.action == "fill"
    ):
        return {}
    # A recording that already searches the destination does not need a second search.
    if any(
        candidate.action == "fill"
        and any(
            str(rung.get("role", "")).lower() == "searchbox" for rung in candidate.targets or []
        )
        for candidate in candidates
    ):
        return {}
    new_click = next(
        (
            candidate
            for candidate in candidates
            if candidate.action == "click"
            and any(
                str(rung.get("name", "")).strip().lower() == "new"
                for rung in candidate.targets or []
            )
        ),
        None,
    )
    if new_click is None or not new_click.recorded_id:
        return {}
    column = options.record_key_column or "email"
    search_targets = [
        {"by": "role", "role": "searchbox", "name": "Search..."},
        {"by": "text_fuzzy", "value": "Search..."},
    ]
    bodies: list[dict[str, Any]] = [
        {
            "action": "fill",
            "name": "Search the attendees for this email",
            "intent": "search the attendees for the row's email",
            "targets": [dict(rung) for rung in search_targets],
            "value": {"kind": "input", "column": column},
            "clear_first": True,
        },
        {
            "action": "press",
            "name": "Run the attendee search",
            "intent": "run the attendee search",
            "targets": [dict(rung) for rung in search_targets],
            "key": "Enter",
        },
        {
            "action": "branch",
            "name": "Skip a row whose email is already registered",
            "when": {
                "element_present": {
                    "targets": [{"by": "text_fuzzy", "value": f"{{{{row.{column}}}}}"}],
                    "intent": "the attendee with this email address",
                }
            },
            "then": [
                {
                    "id": "end-record-1",
                    "action": "end_record",
                    "outcome": "skipped",
                    "reason": "Already there: this email is already registered.",
                }
            ],
        },
    ]
    warnings.append(
        "this create looks for the row's email before adding it, so an attendee already there "
        "is not added twice"
    )
    return {new_click.recorded_id: bodies}


def _rewrite_container_row_clicks(candidates: list[Candidate]) -> None:
    """A click that landed on a container is re-aimed at the row's own value (PL-123 Events).

    Odoo records the event card's whole ``<main>`` area, with every text run glued into one name.
    The event is still opened by its own name; a text rung by the row's value finds the card and
    never the container.
    """
    for candidate in candidates:
        if candidate.action != "click":
            continue
        if not any(str(rung.get("role", "")).lower() == "main" for rung in candidate.targets or []):
            continue
        template = next(
            (
                text
                for rung in candidate.targets or []
                for text in (rung.get("name"), rung.get("text"), rung.get("value"))
                if isinstance(text, str) and "{{row." in text
            ),
            None,
        )
        if template is not None:
            candidate.targets = [{"by": "text_fuzzy", "value": template}]
            candidate.name = f"Click {template}"[:120]


def _render_candidate(candidate: Candidate, options: CompileOptions) -> dict[str, Any]:
    if candidate.action == "navigate":
        # The schema's step_navigate allows nothing but id, action, name and url.
        return {
            "action": "navigate",
            "name": candidate.name or f"Open {path_pattern(candidate.url) or candidate.url}",
            "intent": _intent_for(candidate, options),
            "url": candidate.url,
        }
    step: dict[str, Any] = {"action": candidate.action}
    step["name"] = candidate.name or _default_name(candidate.action, candidate.targets)
    step["intent"] = _intent_for(candidate, options)
    step["targets"] = candidate.targets
    if candidate.frame:
        step["frame"] = candidate.frame
    if candidate.action == "press":
        # The schema's step_press carries no commit and no assert_after -- a press alone changes no
        # destination record -- so neither is ever emitted for one.
        step["key"] = candidate.key
        return step
    if candidate.action == "check":
        # PL-114: a check sets a yes/no to the value a run should leave behind, and carries the
        # state the demonstration left when the recording saw it. It is not a fill: no clear_first
        # and no option_match.
        step["value"] = candidate.value
        if candidate.checked is not None:
            step["checked"] = candidate.checked
    if candidate.action in ("fill", "select"):
        step["value"] = candidate.value
        if candidate.action == "select":
            step["option_match"] = "text"
        else:
            step["clear_first"] = True
            # A fill the recorder witnessed as an autocomplete accept carries the witness onto the
            # step, so the executor knows to complete it by containment rather than move on (§4.3).
            if candidate.autocomplete:
                step["autocomplete"] = True
    if candidate.commit:
        step["commit"] = True
        step["assert_after"] = candidate.assert_after
    return step


def _literal(value: Any) -> Any:
    """The literal a recorded value carries, or None when it is not a constant."""
    if isinstance(value, dict) and value.get("kind") == "constant":
        return value.get("value")
    return None


def _fold_chip_fills(candidates: list[Candidate]) -> tuple[list[Candidate], list[str]]:
    """Fold a chip's fill and the prefix fill it superseded into one step (PL-018, §4.1.2).

    The recorder witnessed the relationship, so it rides the envelope: the chip-add fill carries
    ``chip``, ``autocomplete``, and the id of the prefix fill it ``supersedes``. When the guards
    hold, the prefix fill and the accept step between the two (a ``select`` on the field, or a
    ``press``) are dropped, and the chip's own fill -- the chip's value, never the typed text -- is
    what remains. When a guard does not hold nothing is folded, and the warning names the guard that
    failed: the one place a fold could hide a wrong value, so the compiler refuses to guess.
    """
    warnings: list[str] = []
    dropped: set[int] = set()

    for index, candidate in enumerate(candidates):
        if not candidate.supersedes:
            continue
        prefix = next(
            (
                position
                for position in range(index)
                if candidates[position].recorded_id == candidate.supersedes
            ),
            None,
        )
        prefix_message = (
            f"the chip fill at event {candidate.seq} supersedes {candidate.supersedes!r}"
        )
        if prefix is None or candidates[prefix].action != "fill":
            warnings.append(
                f"{prefix_message}, which is not an earlier fill; it stands as recorded"
            )
            continue
        superseded = candidates[prefix]
        if target_signature(superseded.targets) != target_signature(candidate.targets):
            warnings.append(f"{prefix_message} a fill of another field; it stands as recorded")
            continue
        if not candidate.autocomplete:
            warnings.append(
                f"the chip fill at event {candidate.seq} carries no autocomplete witness; "
                "it stands as recorded"
            )
            continue
        typed = _literal(superseded.value)
        chip_value = _literal(candidate.value)
        if typed and str(typed).lower() not in str(chip_value).lower():
            warnings.append(
                f"the chip's value {chip_value!r} does not contain the typed text {typed!r}; "
                "the fills stand as recorded"
            )
            continue

        dropped.add(prefix)
        for position in range(prefix + 1, index):
            accept = candidates[position]
            same_field = target_signature(accept.targets) == target_signature(candidate.targets)
            # The accept the fold consumes: a key press, a select of the same field, or the click
            # on the suggestion itself (the ticket's "by click, key or paste"). A click on the
            # option is the same action as the chip that followed it, and leaving it in would make
            # the replay accept a suggestion twice.
            if (
                accept.action == "press"
                or (accept.action == "select" and same_field)
                or (accept.action == "click" and _is_option_click(accept))
            ):
                dropped.add(position)
        warnings.append(
            f"folded the fill superseded by the chip fill at event {candidate.seq} into the chip"
        )

    if not dropped:
        return list(candidates), warnings
    return [item for position, item in enumerate(candidates) if position not in dropped], warnings


def _is_option_click(candidate: Candidate) -> bool:
    """Whether a click's ladder names a suggestion option (the accept, not an ordinary click)."""
    return any(str(rung.get("role", "")).lower() == "option" for rung in candidate.targets or [])


def _option_text(candidate: Candidate) -> str:
    """The text a suggestion click accepted: the option's own accessible name."""
    for rung in candidate.targets or []:
        for key in ("name", "text", "value"):
            text = rung.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _fold_option_fills(candidates: list[Candidate]) -> tuple[list[Candidate], list[str]]:
    """Drop the fill that only restates the suggestion the previous step accepted.

    A suggestion click puts the option's own text into the field, so the value change that follows
    it is the page's doing, not a second edit. Odoo's many2one records exactly that: ``fill
    "Priya"``, ``click "Priya Raman"`` (the option), then ``fill "Priya Raman"`` on the input the
    option filled. The replay has no need of the last one -- the click already put the value there
    -- and typing it again fights the widget: Odoo's own inline completion had left "Priya Rama" in
    the field, so the second fill wrote "Priya RamaPriya Raman" and parked the record (PL-027).

    The value is not lost: the fold drops a *step*, and the popup's sample row still carries the
    column the option rung is templated from (``{{row.salesperson_2}}``), so a row can still name
    the suggestion to take.
    """
    warnings: list[str] = []
    kept: list[Candidate] = []
    for candidate in candidates:
        previous = kept[-1] if kept else None
        if (
            previous is not None
            and candidate.action == "fill"
            and _is_option_click(previous)
            and not candidate.chip
            and isinstance(candidate.value, dict)
            and candidate.value.get("kind") == "constant"
            and str(candidate.value.get("value", "")).strip() == _option_text(previous)
        ):
            warnings.append(
                f"folded the fill at event {candidate.seq} into the suggestion accepted at event "
                f"{previous.seq}: the click already put {_option_text(previous)!r} in the field"
            )
            continue
        kept.append(candidate)
    return kept, warnings


def _first_rung(targets: list[dict[str, Any]]) -> dict[str, Any] | None:
    return targets[0] if targets else None


def _rung_text(rung: dict[str, Any] | None) -> str:
    if rung is None:
        return ""
    for key in ("name", "text", "value"):
        text = rung.get(key)
        if isinstance(text, str):
            return text
    return ""


def _input_labels(candidates: list[Candidate]) -> set[str]:
    """The label/name texts of every fill field in the recording."""
    labels: set[str] = set()
    for candidate in candidates:
        if candidate.action != "fill":
            continue
        for rung in candidate.targets or []:
            for key in ("name", "text", "value"):
                text = rung.get(key)
                if isinstance(text, str) and text.strip():
                    labels.add(text.strip())
    return labels


def _shares_a_target(left: Candidate, right: Candidate) -> bool:
    """Whether two steps name the same element: one rung of their ladders is identical."""
    right_rungs = right.targets or []
    return any(rung in right_rungs for rung in (left.targets or []))


def _fold_select_control_clicks(
    candidates: list[Candidate],
) -> tuple[list[Candidate], list[str]]:
    """Drop a click on the control the very next select opens by itself (PL-078).

    A custom dropdown is opened by clicking its control and chosen by pressing the option. The
    recorder sees both, and records the click on the control as a click step and the press as a
    select of that control. On replay the select already opens the control, so keeping the click
    first toggles the list open and the select's own click closes it again -- OrangeHRM's timesheet
    parked every row that way. Dropping the click leaves one step per action; a click that opened
    a list nobody then chose from has no select after it and is kept.
    """
    warnings: list[str] = []
    kept: list[Candidate] = []
    index = 0
    while index < len(candidates):
        current = candidates[index]
        following = candidates[index + 1] if index + 1 < len(candidates) else None
        if (
            following is not None
            and current.action == "click"
            and following.action == "select"
            and _shares_a_target(current, following)
        ):
            warnings.append(
                f"folded the click at event {current.seq} into the select at event "
                f"{following.seq}: the select opens that control itself"
            )
            index += 1
            continue
        kept.append(current)
        index += 1
    return kept, warnings


def _drop_container_clicks(
    candidates: list[Candidate],
) -> tuple[list[Candidate], list[str]]:
    """Drop clicks on containers, not controls.

    PL-031: a click on a group or card whose text contains the labels of two or more inputs is
    not a control click. So is a bare css-only click with no role, label or test id -- unless the
    recording saw that click move the page (PL-075): Odoo's product list row is a css-only `tr`,
    and clicking it opens the record. A click with navigation evidence is an action, not noise.
    """
    warnings: list[str] = []
    kept: list[Candidate] = []
    labels = _input_labels(candidates)

    for candidate in candidates:
        if candidate.action != "click":
            kept.append(candidate)
            continue

        targets = candidate.targets or []
        # A css-only target with no durable semantic rung is a container, not a control -- unless
        # the click's own observation says it moved the page, which is an action whatever the
        # element was. (`after_url` is the next action's page, not this click's evidence, so it
        # cannot decide this.)
        has_semantic = any(rung.get("by") in ("role", "testid", "label") for rung in targets)
        moved = isinstance((candidate.observation or {}).get("url_matches"), str)
        if not has_semantic and not moved and not _is_back_click(candidate):
            warnings.append(
                f"dropped container click at event {candidate.seq}: "
                "it has no role, label or test id"
            )
            continue

        # A generic-role click whose name/text contains two or more input labels is a group/card.
        first = _first_rung(targets)
        if first and first.get("by") == "role" and str(first.get("role")).lower() == "generic":
            text = candidate.name or _rung_text(first)
            matched = [label for label in labels if label in text]
            if len(matched) >= 2:
                warnings.append(
                    f"dropped container click at event {candidate.seq}: it touched the labels "
                    f"{matched!r}, not a control"
                )
                continue

        kept.append(candidate)

    return kept, warnings


def _click_touches_field(
    click: Candidate,
    field_targets: list[dict[str, Any]],
    field_labels: set[str],
    all_input_labels: set[str],
) -> bool:
    """Whether a click only touched the given field, its options, or a container around it."""
    if target_signature(click.targets) == target_signature(field_targets):
        return True
    if _is_option_click(click):
        return True
    # Pressing a real control is an action of its own, never fiddling with a field -- whatever
    # its text shares with the field's label ("Add" next to "Add recipient").
    first = _first_rung(click.targets)
    if first and str(first.get("role", "")).lower() in {
        "button",
        "link",
        "menuitem",
        "tab",
        "checkbox",
        "radio",
        "switch",
    }:
        return False
    click_text = (
        (click.name or "") + " " + " ".join(_rung_text(rung) for rung in click.targets or [])
    )
    matched = [label for label in field_labels if label in click_text]
    other_labels = all_input_labels - field_labels
    return matched and not any(label in click_text for label in other_labels)


def _fill_text(candidate: Candidate) -> str:
    """The literal a fill typed, when it is still a literal; empty otherwise."""
    value = candidate.value
    if isinstance(value, dict):
        literal = value.get("value")
        return literal.strip() if isinstance(literal, str) else ""
    return value.strip() if isinstance(value, str) else ""


def _click_text(click: Candidate) -> str:
    """Everything a click's name and rungs say about what it pressed."""
    return (click.name or "") + " " + " ".join(_rung_text(r) for r in click.targets or [])


def _fold_retried_fills(candidates: list[Candidate]) -> tuple[list[Candidate], list[str]]:
    """Keep only the last attempt to fill a field before the commit step.

    PL-031: when the same target (equal first rung) is filled again before the commit step, the
    earlier fill and every click between the two fills that only touched that field are dropped.
    """
    warnings: list[str] = []
    if not candidates:
        return candidates, warnings

    # Find the first commit step; retried fills after that are not folded.
    commit_index = next(
        (i for i, candidate in enumerate(candidates) if candidate.commit), len(candidates)
    )
    all_input_labels = _input_labels(candidates)

    dropped: set[int] = set()
    for index, candidate in enumerate(candidates[:commit_index]):
        if candidate.action != "fill" or index in dropped:
            continue
        first = _first_rung(candidate.targets)
        if first is None:
            continue

        # Look for a later fill of the same first rung before the commit step.
        retry_index: int | None = None
        for later in range(index + 1, commit_index):
            later_candidate = candidates[later]
            if later_candidate.action != "fill" or later_candidate.chip:
                continue
            later_first = _first_rung(later_candidate.targets)
            if later_first == first:
                retry_index = later
                break
        if retry_index is None:
            continue
        field_labels: set[str] = set()
        for rung in candidate.targets or []:
            for key in ("name", "text", "value"):
                text = rung.get(key)
                if isinstance(text, str) and text.strip():
                    field_labels.add(text.strip())
        # Two fills of one field are a retry only when nothing between them did anything but
        # fiddle with that field. A button pressed between them (a recipients box's "Add") or a
        # suggestion picked for what was typed (its text contains the typed value) means the
        # first entry was finished, and a second entry follows. Odoo's "Start typing..." hint does
        # not contain the typed value, so a real retry still folds.
        typed = _fill_text(candidate).lower()
        between_clicks = [
            candidates[between]
            for between in range(index + 1, retry_index)
            if candidates[between].action == "click"
        ]
        finished = any(
            (typed and _is_option_click(click) and typed in _click_text(click).lower())
            or not _click_touches_field(click, candidate.targets, field_labels, all_input_labels)
            for click in between_clicks
        )
        if finished or any(
            candidates[between].action not in ("click", "fill")
            for between in range(index + 1, retry_index)
        ):
            continue

        dropped.add(index)
        for between in range(index + 1, retry_index):
            between_candidate = candidates[between]
            if between_candidate.action != "click":
                continue
            if _click_touches_field(
                between_candidate, candidate.targets, field_labels, all_input_labels
            ):
                dropped.add(between)
        warnings.append(
            f"folded the retried fill at event {candidate.seq} into the later fill at event "
            f"{candidates[retry_index].seq}"
        )

    if not dropped:
        return candidates, warnings
    return [item for i, item in enumerate(candidates) if i not in dropped], warnings


_TAB_KEYS = {"tab", "shift+tab"}


def _is_combobox(candidate: Candidate) -> bool:
    first = _first_rung(candidate.targets)
    return bool(first) and str(first.get("role", "")).lower() == "combobox"


# A suggestion box's own prompt, not a record: "Start typing...", "Search More...".
_HINT_OPTION = re.compile(
    r"^(start typing|search more|type to search|no records?)\b|(\.\.\.|…)$", re.I
)


def _is_hint_option(candidate: Candidate) -> bool:
    """Whether an option click picks the box's own prompt rather than a record (PL-037).

    PL-032 dropped every option picked before anything was typed, which also dropped a real
    record picked straight from the open list (the owner chose "Ridgeline Outfitters" that way).
    Only a prompt is dropped now.
    """
    names = [
        str(rung.get("name") or rung.get("value") or "").strip()
        for rung in candidate.targets or []
        if rung.get("by") in ("role", "text_fuzzy")
    ]
    return any(_HINT_OPTION.search(name) for name in names if name)


def _settle_suggestion_boxes(
    candidates: list[Candidate],
) -> tuple[list[Candidate], list[str]]:
    """Make a suggestion box replay the way a person used it.

    PL-032, from the owner's second Odoo recording:

    * An option clicked before anything was typed since the last pick is the box's own hint
      (Odoo's "Start typing..."): it chooses nothing, and on replay it may not exist. Dropped.
    * Typing into a suggestion box and pressing Tab lets the app pick for you. On replay the Tab
      lands before the suggestions have loaded, so nothing is picked and Odoo empties the field.
      The Tab becomes a click on ``Create "<value>"``, which the create-option expansion then turns
      into "the exact name, else Create".
    * A Tab whose target is not a control (a whole-page container) only moved focus. Dropped.
    """
    warnings: list[str] = []
    settled: list[Candidate] = []
    typed_in_box = False
    for index, candidate in enumerate(candidates):
        if candidate.action == "fill":
            # Any typing can open suggestions: Odoo's salesperson box is a plain textbox.
            typed_in_box = True
            settled.append(candidate)
            continue
        if candidate.action == "click" and _is_option_click(candidate):
            if not typed_in_box and _is_hint_option(candidate):
                warnings.append(
                    f"dropped the hint option at event {candidate.seq}: it is the box's own prompt"
                )
                continue
            typed_in_box = False
            settled.append(candidate)
            continue
        if candidate.action == "press" and str(candidate.key or "").lower() in _TAB_KEYS:
            previous = settled[-1] if settled else None
            following = next(
                (
                    c
                    for c in candidates[index + 1 :]
                    if not (c.action == "press" and str(c.key or "").lower() in _TAB_KEYS)
                ),
                None,
            )
            typed = _fill_text(previous) if previous is not None else ""
            if (
                previous is not None
                and previous.action == "fill"
                and _is_combobox(previous)
                and typed
                and not (following is not None and _is_option_click(following))
            ):
                settled.append(
                    replace(
                        candidate,
                        action="click",
                        key=None,
                        name=f'Click Create "{typed}"',
                        targets=[{"by": "role", "role": "option", "name": f'Create "{typed}"'}],
                        recorded_id=f"pick-{candidate.seq}",
                    )
                )
                typed_in_box = False
                warnings.append(
                    f"the Tab at event {candidate.seq} picked a suggestion; it replays as a pick"
                )
                continue
            first = _first_rung(candidate.targets)
            if first is None or str(first.get("role", "")).lower() in ("", "generic"):
                warnings.append(f"dropped the Tab at event {candidate.seq}: it only moved focus")
                continue
        settled.append(candidate)
    return settled, warnings


def _expand_create_option(candidates: list[Candidate]) -> list[Candidate]:
    """Turn a Create "<value>" option click into a ladder that accepts the record first.

    PL-031: an option named ``Create "<value>"``, where ``<value>`` is a fill value just typed,
    is replaced by two role rungs in this order: the exact column value first, then the Create
    template. The runner takes the first rung that matches exactly one element, so an existing
    record is accepted before a new one is created.
    """
    updated: list[Candidate] = []
    for index, candidate in enumerate(candidates):
        if candidate.action != "click" or not _is_option_click(candidate):
            updated.append(candidate)
            continue

        previous_fill = next(
            (
                c
                for c in reversed(candidates[:index])
                if c.action == "fill" and isinstance(c.value, dict)
            ),
            None,
        )
        if previous_fill is None or not isinstance(previous_fill.value, dict):
            updated.append(candidate)
            continue

        column = previous_fill.value.get("column")
        if not isinstance(column, str):
            updated.append(candidate)
            continue

        first = _first_rung(candidate.targets)
        if first is None or first.get("by") != "role":
            updated.append(candidate)
            continue

        name = str(first.get("name", ""))
        expected = f'Create "{{{{row.{column}}}}}"'
        if name != expected:
            updated.append(candidate)
            continue

        exact = {"by": "role", "role": "option", "name": f"{{{{row.{column}}}}}"}
        new_targets = [exact, *candidate.targets]
        updated.append(replace(candidate, targets=new_targets))
    return updated


def _refuse_what_cannot_replay(events: list[Event], candidates: list[Candidate]) -> None:
    """Refuse a recording the compiler cannot replay safely (PL-018, §3.8 and §4.4).

    A press is never commit-capable, so a recording whose last action is a committing key, or whose
    named commit step is a press, would replay with no write-ahead around the send. A chip removed
    without its own control was recorded as nothing at all, and a replay would keep a recipient the
    person removed. Neither is a warning: a warning in a list is easy to miss.
    """
    last_actionable = next(
        (event for event in reversed(events) if event.kind in ACTIONABLE_KINDS), None
    )
    if (
        last_actionable is not None
        and last_actionable.kind == "press"
        and (last_actionable.step or {}).get("key") in COMMITTING_KEYS
    ):
        raise CompileError(KEY_COMMIT_REFUSAL)
    if any(candidate.action == "press" and candidate.commit for candidate in candidates):
        raise CompileError(KEY_COMMIT_REFUSAL)
    if any(event.kind == UNRECORDABLE_EVENT_KIND for event in events):
        raise CompileError(UNRECORDABLE_REFUSAL)


def _render_steps(
    candidates: list[Candidate],
    options: CompileOptions,
    *,
    duplicate_branch_after: int | None = None,
    duplicate_branch: dict[str, Any] | None = None,
    already_branch_before: int | None = None,
    already_branch: dict[str, Any] | None = None,
    extra_before: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    bodies: list[dict[str, Any]] = []

    # A recording that already opens with a navigation carries its own first navigate step, so the
    # synthesized opening one is not added on top of it.
    opens_with_navigate = bool(candidates) and candidates[0].action == "navigate"
    if options.include_navigate and not opens_with_navigate:
        start = options.start_url or (candidates[0].url if candidates else "")
        if start:
            bodies.append(
                {
                    "action": "navigate",
                    "name": f"Open {path_pattern(start) or start}",
                    "intent": f"open {path_pattern(start) or start}",
                    "url": start,
                }
            )

    for index, candidate in enumerate(candidates):
        # PL-123: a create's duplicate search goes immediately before the New click.
        for body in (extra_before or {}).get(candidate.recorded_id or "", []):
            bodies.append(body)
        # PL-079: the "nothing to save" check goes immediately before the commit click.
        if already_branch is not None and index == already_branch_before:
            bodies.append(already_branch)
        bodies.append(_render_candidate(candidate, options))
        # PL-017: the duplicate check goes straight after the click that ran the search.
        if duplicate_branch is not None and index == duplicate_branch_after:
            bodies.append(duplicate_branch)

    counters: Counter[str] = Counter()
    steps: list[dict[str, Any]] = []
    for body in bodies:
        counters[body["action"]] += 1
        steps.append({"id": f"{body['action']}-{counters[body['action']]}", **body})
    return steps


def _is_option_pick(candidate: Candidate) -> bool:
    """Whether a click accepts a suggestion from a dropdown rather than running a search.

    PL-049: the product field's placeholder is "Search a product", and the recording reads that
    placeholder back as the field's accessible name. The click that follows is a pick -- its
    ladder names an ``option``, or the dropdown that holds one -- not a search. Treating it as a
    search would make every row skip itself as a duplicate.
    """
    if candidate.action != "click":
        return False
    for rung in candidate.targets or []:
        if str(rung.get("role", "")).lower() == "option":
            return True
        within = rung.get("within")
        if isinstance(within, str):
            container = within.lower()
            if "listbox" in container or "autocomplete" in container:
                return True
    return False


def _looks_like_a_search(fill: Candidate, runner: Candidate) -> bool:
    """Whether a fill-then-run pair reads as a search, not any other fill-then-run.

    A wizard's ``fill email -> Next`` is a fill immediately followed by a click too, and mistaking
    it for a search would turn on the duplicate branch and silently skip records. The one durable
    signal a recording carries is the control's own name: a search box or a search button says
    "search". Erring the other way is safe -- a search that is not recognised simply gets no branch
    -- so a pair with no such signal is not a search.

    PL-049: a click that accepts a suggestion from a dropdown is a pick, not a search, even when
    the field's name or placeholder says "Search a product". A ``text_fuzzy`` rung is the
    recorder's placeholder fallback, so a "search" found only there is not durable enough to
    switch the duplicate branch on.
    """
    if _is_option_pick(runner):
        return False
    for candidate in (fill, runner):
        for rung in candidate.targets or []:
            if str(rung.get("by", "")).lower() == "text_fuzzy":
                continue
            if str(rung.get("role", "")).lower() == "searchbox":
                return True
            within = rung.get("within")
            if isinstance(within, str) and "search" in within.lower():
                return True
            for key in ("name", "value", "text"):
                text = rung.get(key)
                if isinstance(text, str) and "search" in text.lower():
                    return True
    return False


def _runs_it(candidate: Candidate) -> bool:
    """Whether this step is the one that *runs* a search, rather than typing into it.

    A search is run by clicking its control, or by pressing Enter in its box: a search field that
    applies on Enter -- Odoo's own search view, and most application search bars -- has no button
    to click at all, so a press is the only thing in the recording that says "run this".
    """
    if candidate.action == "click":
        return True
    return candidate.action == "press" and str(candidate.key or "") == "Enter"


def detect_duplicate_search(
    candidates: list[Candidate], options: CompileOptions
) -> tuple[int, str] | None:
    """The step that runs a search whose result says whether the record is already there.

    Returns ``(index of the step that runs the search, the column it searched for)``. The column is
    what the destination was searched by, which is not necessarily the ledger's record key: a
    workflow may be keyed on a name while the destination is searched by email, and the duplicate
    branch has to read the page the *search* produced.

    The search is a ``fill`` whose recorded value is one of the sample row's cells, followed by the
    step that runs it (a click, or Enter), with the create's own commit still to come. The
    "looks like a search" check and the later commit are what separate a search from a create that
    merely fills the value, or from a wizard's ``fill -> Next``. No search means no branch; the
    compiler never invents one.
    """
    if not options.duplicate_check or not options.sample_row:
        return None

    found: list[tuple[int, str]] = []
    for index, candidate in enumerate(candidates):
        if candidate.action != "fill":
            continue
        value = candidate.value
        if not (isinstance(value, dict) and value.get("kind") == "constant"):
            continue
        column = match_column(value.get("value"), options.sample_row)
        if column is None:
            continue
        if index + 1 >= len(candidates) or not _runs_it(candidates[index + 1]):
            continue
        if not _looks_like_a_search(candidate, candidates[index + 1]):
            continue
        # A create still commits after the search; the search itself is not the create.
        if not any(later.commit for later in candidates[index + 2 :]):
            continue
        found.append((index + 1, column))

    if not found:
        return None
    # The ledger's own key column wins when the demonstration searched for it, so a recipe keyed on
    # the email keeps behaving exactly as it did before this generalisation.
    for run_index, column in found:
        if column == options.record_key_column:
            return run_index, column
    return found[0]


_UUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def _opens_a_record(candidate: Candidate) -> bool:
    """Whether this click navigated to a record's own page (PL-077).

    The one signal is in the URLs the recording already carries: the page the click ran on and the
    page it landed on. A *record page* is a page whose last path segment is all digits
    (``/odoo/products/17``); a create page names its purpose in words (``/odoo/products/new``),
    and a click that changes nothing leaves the URL where it was. Both are read from the URLs
    only -- never from the app's name, its version, or anything the page says.

    An update-in-place click (a row that opens in a dialog, say) lands on the same URL and is
    therefore *not* a record page; that shape stays a create, which is today's behaviour.
    """
    landed = candidate.after_url or ""
    started = candidate.url or ""
    if not landed or landed == started:
        return False
    if not origin_of(landed) or origin_of(landed) != origin_of(started):
        return False
    tail = urlsplit(landed).path.rstrip("/").rsplit("/", 1)[-1]
    # A numeric id (Odoo) or a UUID (Twenty and most newer apps) names one record.
    return tail.isdigit() or _UUID.fullmatch(tail) is not None


def detect_find_then_update(candidates: list[Candidate], run_index: int) -> bool:
    """Whether a searched recording is a find-then-update rather than a create (PL-077).

    The rule, decided from the recorded steps and their URLs alone:

    * after the step that runs the search, and before the first commit that follows it, a click
      navigates to a **record page** -- a page on the same origin whose last path segment is all
      digits, different from the page the click ran on; and
    * there is a commit after that click (the caller only searches for recordings that commit).

    Then the search finds the record the run is about to change: the duplicate check must not skip
    it, and a search that finds nothing skips the row instead (there is nothing there to change).
    Everything else is a create: no search, a click to a page whose last segment is a word
    (``.../new``), a click that does not navigate, or a commit before any such click.
    """
    first_commit = next(
        (index for index in range(run_index + 1, len(candidates)) if candidates[index].commit),
        None,
    )
    if first_commit is None:
        return False
    return any(
        candidate.action == "click" and _opens_a_record(candidate)
        for candidate in candidates[run_index + 1 : first_commit]
    )


def _duplicate_branch(value_template: str) -> dict[str, Any]:
    """A branch that ends a record ``skipped`` when the search already shows its value.

    The condition is deliberately *not* a page-wide text search. Odoo renders the query it is
    searching for back onto the page -- the search view draws a facet holding exactly the value
    that was typed -- so "that text is visible anywhere" is true for a record that does not exist
    and the branch would skip the whole batch. What the destination's own answer looks like is a
    cell in its results table holding the value, which is rung 1 of the ladder and therefore the
    most durable rung a condition can carry.
    """
    return {
        "action": "branch",
        "name": "Skip a record if the search finds it",
        "when": {
            "element_present": {
                "targets": [
                    {"by": "role", "role": "cell", "name": value_template},
                ]
            }
        },
        "then": [
            {
                "id": "end-record-1",
                "action": "end_record",
                "outcome": "skipped",
                "reason": "duplicate",
            }
        ],
    }


def _not_found_branch(column: str, value_template: str) -> dict[str, Any]:
    """A branch that ends a record ``parked`` when the search finds nothing (PL-077, PL-106).

    The mirror image of :func:`_duplicate_branch`, and built from the same machinery: the same
    ``element_absent`` condition shape the schema already has (no new keys), reading the same
    results cell. For an update, an empty result means the record the row would change is not in
    the app, so carrying on would type into whatever page the run happens to be on.

    It parks rather than skips (PL-106 review, restored by T1's review of PL-113).
    ``element_absent`` is not proof the record is gone: it is also true before a slow results table
    has drawn, and on a login wall, an expired-session dialog or an error page -- the executor's
    wall probe runs only
    when a check answers false, not when an absence answers true. A skip is final, so a skip here
    would lose the row for good. Parking puts the row in front of a person, who can skip it in one
    click -- the run page's **Skip it** button -- after looking. It never ends ``failed``: a
    recipe's ``end_record`` cannot fail a record (D-017).

    PL-113 item 6 asked for the skip, and its review sent that back as the ticket's error. What is
    kept from it is the plain sentence: the column's own name and the value the row searched for, so
    the parked row says exactly what was not found.

    Ledger: the branch runs after the search and before any commit, so the record is in ``filling``
    with ``commit_attempted`` false; ``filling -> parked`` is legal there, and the person's skip is
    the ordinary ``parked -> skipped`` dismiss (migration 0003). ``run_loop.end_record`` still turns
    a commit-attempted skip into a park (B2).
    """
    label = " ".join(str(column).replace("_", " ").split()).lower()
    row_value = f"{{{{row.{column}}}}}"
    return {
        "action": "branch",
        "name": "Park a record when the search finds nothing",
        "when": {
            "element_absent": {
                # PL-123: the result is not a table cell. Odoo shows the same search as a kanban
                # card or as a table row, so the ladder names the row's own value and the intent
                # tells a run what it is looking for; PL-122 finds the card or the row.
                "targets": [{"by": "text_fuzzy", "value": row_value}],
                "intent": f"the search result for {row_value}",
            }
        },
        "then": [
            {
                "id": "end-record-1",
                "action": "end_record",
                "outcome": "parked",
                "reason": f"No {label} called ‘{{{{row.{column}}}}}’ was found.",
            }
        ],
    }


def _already_matches_branch(commit_control: Any) -> dict[str, Any] | None:
    """A branch that ends a record ``skipped`` when its values are already on the page (PL-079).

    A form keeps its Save control hidden while it is clean, so a row whose values the page already
    shows has nothing to save: the commit click cannot be prepared, and the record would park. The
    condition is the same ``element_absent`` on the commit control that PL-075 reads as the
    commit's proof -- absent *before* the click means the page has nothing to save.

    ``commit_control`` is the ``element_absent`` selector that commit's assertion carries, or
    ``None`` when the commit proves itself another way; a commit that never watches a control
    disappear cannot say whether there was anything to save, so it gets no branch.

    The branch is the recipe's own step, visible in the recipe. The runner has no special case for
    it (D-017): an ``end_record`` in a ``branch`` is machinery the schema already has.
    """
    if not isinstance(commit_control, dict):
        return None
    targets = commit_control.get("targets")
    if not isinstance(targets, list) or not targets:
        return None
    return {
        "action": "branch",
        "name": "Skip a record whose values are already there",
        # T1 review: a row with work waits this long for Save to go away before carrying on, so the
        # wait is short; a page that already matches shows no Save at once.
        "when": {
            "element_absent": {"targets": [dict(rung) for rung in targets]},
            "timeout_ms": 1000,
        },
        "then": [
            {
                "id": "end-record-1",
                "action": "end_record",
                "outcome": "skipped",
                "reason": "Already had these values, so nothing was changed.",
            }
        ],
    }


def _already_matches_placement(
    candidates: list[Candidate], search_kind: str | None
) -> tuple[int | None, dict[str, Any] | None]:
    """Where the "already matches" branch goes, and the branch itself (PL-079).

    Only an update has one: its commit is the Save on the record's own page, and a page that
    already shows the row's values keeps that Save hidden. The place is immediately before the
    first commit whose assertion is PL-075's disappeared-control proof.
    """
    if search_kind != "update":
        return None, None
    for index, candidate in enumerate(candidates):
        if not candidate.commit:
            continue
        assertion = candidate.assert_after if isinstance(candidate.assert_after, dict) else {}
        branch = _already_matches_branch(assertion.get("element_absent"))
        if branch is not None:
            return index, branch
    return None, None


def _update_columns(candidates: list[Candidate], search_run: int) -> list[str]:
    """The columns the update's own fills write, in step order (PL-079).

    Only the fills after the step that ran the search and before the commit are the update's own:
    the search field is how the record is found, not a value the workflow writes. A fill whose
    value stayed a literal -- a column the sample row does not carry -- is not a column the key can
    read, so it is left out rather than guessed at.
    """
    first_commit = next(
        (index for index in range(search_run + 1, len(candidates)) if candidates[index].commit),
        len(candidates),
    )
    columns: list[str] = []
    for candidate in candidates[search_run + 1 : first_commit]:
        if candidate.action != "fill" or not isinstance(candidate.value, dict):
            continue
        if candidate.value.get("kind") != "input":
            continue
        column = candidate.value.get("column")
        if isinstance(column, str) and column and column not in columns:
            columns.append(column)
    return columns


def _is_record_page(url: str) -> bool:
    """Whether a page's last path segment names one record (a number or a UUID)."""
    path = urlsplit(url).path.rstrip("/")
    if not path:
        return False
    tail = path.rsplit("/", 1)[-1]
    return tail.isdigit() or _UUID.fullmatch(tail) is not None


def _field_label(fill: Candidate) -> str | None:
    """The changed field's own name, from the first rung that carries one."""
    for rung in fill.targets or []:
        if not isinstance(rung, dict):
            continue
        if rung.get("by") == "role" and isinstance(rung.get("name"), str):
            name = rung["name"].strip()
            if name:
                return name
        if rung.get("by") == "label" and isinstance(rung.get("text"), str):
            text = rung["text"].strip()
            if text:
                return text
        if rung.get("by") in ("testid", "text_fuzzy") and isinstance(rung.get("value"), str):
            value = rung["value"].strip()
            if value:
                return value
    return None


def _read_back_assertion(fill: Candidate, options: CompileOptions) -> dict[str, Any] | None:
    """A ``value_equals`` check that the changed field shows the row's value.

    The selector is the field's own target ladder, so it reads the same control the demonstration
    typed into. The value is the row's column, never the taught literal: a batch's second row has
    a different price. A literal the sample row does not carry cannot be read back for every row,
    so no assertion is built for it.
    """
    value = fill.value
    if not isinstance(value, dict):
        return None
    if value.get("kind") != "input" or not isinstance(value.get("column"), str):
        return None
    if not value["column"] or not fill.targets:
        return None
    selector: dict[str, Any] = {"targets": [dict(rung) for rung in fill.targets]}
    if fill.frame:
        selector["frame"] = [dict(step) for step in fill.frame]
    label = _field_label(fill)
    if label is not None:
        selector["description"] = label
    return {
        "value_equals": {
            "selector": selector,
            "value": dict(value),
            # PL-118: a person reads "Sales Price" and a page may write "sales price"; the read-back
            # compares text trimmed and without case. Numbers are compared as numbers by the
            # executor, so "799" and "$ 799.00" are the same value.
            "case_insensitive": True,
        },
        "timeout_ms": options.assertion_timeout_ms,
    }


def _click_touches_a_changed_field(
    click: Candidate, fill_indexes: list[int], candidates: list[Candidate]
) -> bool:
    """Whether a click after the change is still part of editing a field, not a stray one.

    Opening a dropdown or accepting a suggestion belongs to the fill; a click on All Apps or a
    tab strip does not. This is what tells a leave from the stray clicks the owner made while
    looking for the way back, when teaching has not cleaned them away.
    """
    if _is_option_click(click):
        return True
    return any(_shares_a_target(click, candidates[index]) for index in fill_indexes)


def _edit_shape(candidates: list[Candidate]) -> dict[str, Any] | None:
    """The shape of an edit: the click that opened the item, its fills, and the leave.

    ``open`` is the last click that landed on a record's own page (a numeric id or a UUID). The
    changed fields are the parameterized fills after that click and on the same path. ``leave`` is
    the first thing that took the person off the item's page: a click that moved the page (a
    breadcrumb, the next item), a navigation, or the app's save shortcut (Control+Enter or
    Meta+Enter) when one was recorded. A create never has this shape -- ``/new`` is not a record
    page -- so today's create rules are untouched.

    This shape is only used by "the app saves by itself": review B1 keeps a Save-click edit on
    PL-075's proof, because a read-back on that click is already true when the save is sent.
    """
    limit = len(candidates)
    open_index: int | None = None
    for index in range(limit):
        candidate = candidates[index]
        if candidate.action == "click" and _opens_a_record(candidate):
            open_index = index
    if open_index is None:
        return None
    record_url = candidates[open_index].after_url or ""
    record_path = urlsplit(record_url).path.rstrip("/")
    if not record_path:
        return None
    fills: list[int] = []
    leave: int | None = None
    stray_after_change = False
    for index in range(open_index + 1, limit):
        candidate = candidates[index]
        path = urlsplit(candidate.url).path.rstrip("/")
        if path == record_path:
            if (
                candidate.action == "fill"
                and isinstance(candidate.value, dict)
                and candidate.value.get("kind") == "input"
            ):
                fills.append(index)
            # PL-123: a click that names a control going back is a leave even when the recorder
            # saw no landing page for it. The owner taught the narrow window's arrow button; the
            # wide work window draws a breadcrumb link instead, and no URL follows the last click
            # of a recording. Keep scanning: the LAST leave wins, so a stray click earlier in the
            # form cannot stand in for the real one.
            if candidate.action == "click" and _is_back_click(candidate):
                leave = index
            elif (
                candidate.action == "click"
                and candidate.after_url
                and candidate.after_url != candidate.url
            ):
                # The click's own landing page moved: the person left the item.
                leave = index
            elif candidate.action == "click" and not _click_touches_a_changed_field(
                candidate, fills, candidates
            ):
                # A click after the change that teaching did not drop could be the save itself.
                # Without cleaning, the compiler cannot tell the leave from the stray clicks the
                # owner made on the way back; it says so rather than guessing.
                stray_after_change = True
            continue
        if candidate.action == "navigate":
            leave = index
            continue
        if candidate.action == "press" and str(candidate.key or "") in SAVE_SHORTCUT_KEYS:
            # "If none was recorded, use the app's save shortcut only if one was recorded."
            leave = index
            continue
        if (
            candidate.action == "click"
            and candidate.after_url
            and candidate.after_url != candidate.url
        ):
            leave = index
            continue
    if stray_after_change:
        leave = None
    return {
        "open": open_index,
        "record_path": record_path,
        "fills": fills,
        "leave": leave,
        "stray_after_change": stray_after_change,
    }


def _self_save_commit(
    candidates: list[Candidate], options: CompileOptions, warnings: list[str]
) -> bool:
    """Add (or mark) the commit for "the app saves by itself when I leave the page".

    The person's own leave is kept as a step. The commit then re-opens the item with the click
    that opened it during teaching, and its ``value_equals`` check reads the changed field on the
    item's saved page. That is a click commit under the existing rules: write-ahead, reconcile,
    verify_commit. A recording that never left the form proves nothing about a save, so the
    compile says so and appends an ``end_record`` that parks every row -- it can never be Done.
    """
    shape = _edit_shape(candidates)
    if shape is None or not shape["fills"]:
        warnings.append(
            "this recording changes no field on an existing item, so the app's own save cannot be "
            "read back; every row will be left for you"
        )
        return False
    last_fill = shape["fills"][-1]
    assertion = _read_back_assertion(candidates[last_fill], options)
    if assertion is None:
        warnings.append(
            "the value this recording changes is not one of the row's columns, so its saved "
            "value cannot be read back; every row will be left for you"
        )
        return False
    leave = shape["leave"]
    if leave is None:
        # Nothing left the form, so nothing shows what the app saved. Committing the fill itself
        # would check the value the run just typed -- true whatever the app did. The compile says
        # so instead, and every row is left for a person.
        if shape.get("stray_after_change"):
            warnings.append(
                "the recording has clicks after the change that teaching did not drop, so the "
                "leave could not be recognised; every row will be left for you"
            )
        else:
            warnings.append(
                "the recording ends on the form without leaving it, so nothing shows what the app "
                "saved; every row will be left for you"
            )
        return False
    # The item must be opened again to read the saved page. Reuse the recorded click that opened
    # it: its ladder is the one the demonstration proved on this site. If the person already
    # re-opened it after leaving, mark that click instead of adding a duplicate.
    opener = candidates[shape["open"]]
    signature = target_signature(opener.targets)
    commit_index: int | None = None
    for index in range(leave + 1, len(candidates)):
        candidate = candidates[index]
        if candidate.action == "click" and target_signature(candidate.targets) == signature:
            commit_index = index
            break
    if commit_index is not None:
        candidates[commit_index].commit = True
        candidates[commit_index].assert_after = assertion
        candidates[commit_index].intent = candidates[commit_index].intent or (
            "open the item the search found to read the saved value back"
        )
    else:
        candidates.append(
            Candidate(
                action="click",
                targets=[dict(rung) for rung in opener.targets],
                name=opener.name,
                intent="open the item again to read the saved value back",
                url=opener.url,
                after_url=opener.after_url,
                commit=True,
                assert_after=assertion,
                frame=opener.frame,
            )
        )
    warnings.append(
        "the app saves by itself when the page is left; the row is done only when the item's "
        "saved page shows the new value"
    )
    return True


def _resolve_commit_assertion(
    candidate: Candidate, options: CompileOptions
) -> dict[str, Any] | None:
    """The assertion for a commit step, from the most authoritative source first.

    1. an assertion the caller supplied for this recorded step id — a person's
       decision (the editor's "mark Save as a commit step") outranks inference;
    2. what the recording observed after the action;
    3. a navigation the recording observed.
    """
    supplied = (options.commit_assertions or {}).get(candidate.recorded_id or "")
    if supplied:
        return dict(supplied)
    return infer_assertion(
        candidate.observation,
        candidate.url,
        candidate.after_url,
        options.assertion_timeout_ms,
        sample_values=options.sample_row.values(),
    )


def _required_columns(options: CompileOptions) -> set[str]:
    """Columns the input schema marks required, plus any park_fields."""
    required: set[str] = set()
    if options.input_fields:
        for column, spec in options.input_fields.items():
            if isinstance(spec, dict) and spec.get("required"):
                required.add(column)
    if options.park_fields:
        required.update(options.park_fields)
    return required


def _page_change_start(prior_candidates: list[Candidate], save_path: str) -> int:
    """PL-044: where the entry a save belongs to begins among the steps before it.

    A recording may hold an abandoned entry before the real one: a New click, a
    stray pick, a visit to a record's own page. Walking back from the save, the
    last step that moved the page ends everything before it -- a navigate step, a
    step whose after_url landed on a path other than the save's page, or an
    observation whose url_matches does not fit the save's page. With no page
    change the whole recording is one entry, which is the behavior before
    PL-044. A candidate whose observation carries no URL says nothing about the
    page, so it never counts as a page change.
    """
    start = 0
    for index, previous in enumerate(prior_candidates):
        if previous.action == "navigate":
            start = index + 1
            continue
        if previous.after_url and urlsplit(previous.after_url).path != save_path:
            start = index + 1
            continue
        pattern = (previous.observation or {}).get("url_matches")
        if isinstance(pattern, str) and pattern:
            try:
                if re.fullmatch(pattern, save_path) is None:
                    start = index + 1
            except re.error:
                pass
    return start


def _infer_assertion_from_fills(
    candidate: Candidate,
    prior_candidates: list[Candidate],
    options: CompileOptions,
) -> dict[str, Any] | None:
    """Infer a commit assertion from the fills before a commit that showed no evidence.

    PL-031: a quick-create save changes no URL and shows no message. The only evidence is the
    new record appearing. A commit qualifies only when one of the fills before it was templated
    from the record key or a required column; the text it then waits for is the FIRST templated
    fill of the form, because that is the field a list or kanban card shows as the record's name
    (Odoo's quick-create card shows the contact, never the email the key may be).

    PL-044: only the entry the save belongs to counts -- the steps after the last page
    change before the save. An abandoned entry before it (a stray pick on the same page,
    a visit to another record) must not become the save's check.
    """
    if candidate.action != "click" or candidate.observation or candidate.after_url:
        return None
    commit_ids = set(options.commit_step_ids or ())
    if candidate.recorded_id not in commit_ids:
        return None

    required = _required_columns(options)
    record_key_column = options.record_key_column
    columns: list[str] = []
    # PL-037: the first thing entered in the form, typed or picked. A contact picked straight from
    # the open list is a record's name the new card shows, just like a typed one.
    first_entry: str | None = None
    entry_start = _page_change_start(prior_candidates, urlsplit(candidate.url).path)
    for previous in prior_candidates[entry_start:]:
        if previous.action == "click" and _is_option_click(previous) and first_entry is None:
            picked = next(
                (
                    str(rung.get("name") or "").strip()
                    for rung in previous.targets or []
                    if rung.get("by") == "role" and rung.get("role") == "option"
                ),
                "",
            )
            if picked and not _HINT_OPTION.search(picked):
                first_entry = picked
            continue
        if previous.action != "fill" or not isinstance(previous.value, dict):
            continue
        column = previous.value.get("column")
        if isinstance(column, str):
            columns.append(column)
            if first_entry is None:
                first_entry = f"{{{{row.{column}}}}}"

    if not any(column == record_key_column or column in required for column in columns):
        return None
    if first_entry is None:
        return None

    # PL-032: the name is already on screen in the form being filled, so "it is visible" proves
    # nothing; "it appears once more than before the click" is the new card itself.
    return {
        "text_count_increases": first_entry,
        "timeout_ms": options.assertion_timeout_ms,
    }


def _chrome_names(candidates: list[Candidate]) -> set[str]:
    """The names of the page's own frame: its tabs, links and menu items (PL-113 item 1).

    A recording that clicks the Sales tab on its way to a product puts "Sales" in the steps. If a
    save's check later waits for that same word, the check is the tab -- true on every page -- and
    not a change. The names are read from the recorded clicks' first rung, lower-cased.
    """
    names: set[str] = set()
    for candidate in candidates:
        if candidate.action != "click":
            continue
        for rung in candidate.targets or []:
            if rung.get("by") != "role":
                continue
            if str(rung.get("role", "")).lower() not in CHROME_ROLES:
                continue
            name = rung.get("name")
            if isinstance(name, str) and name.strip():
                names.add(name.strip().lower())
    return names


def _is_chrome_assertion(assert_after: Any, chrome_names: set[str]) -> str | None:
    """The frame text a commit's check waits for, when that text is the page's own frame."""
    if not isinstance(assert_after, dict) or not chrome_names:
        return None
    text = assert_after.get("text_visible")
    if not isinstance(text, str):
        return None
    return text.strip() if text.strip().lower() in chrome_names else None


def compile_recording(recording: Any, options: CompileOptions) -> CompileResult:
    """Compile a recorded event stream into a recipe that matches the schema.

    Raises :class:`CompileError` when the recording cannot become a valid recipe
    — no usable actions, no record key, or a commit with nothing to assert.
    """
    events, warnings = parse_events(recording)
    if not events:
        raise CompileError("the recording has no click, fill, select, or submit events")

    events, fold_warnings = coalesce_fills(events)
    warnings.extend(fold_warnings)
    events, press_warnings = collapse_presses(events)
    warnings.extend(press_warnings)
    events, submit_warnings = collapse_submits(events)
    warnings.extend(submit_warnings)

    # PL-117 item 1: a sign-in saves nothing in the destination, so it is compiled exactly as
    # PL-112's "Nothing gets saved": no step is a commit and no step carries an inferred landing
    # check. `sign-in-finished` is the only proof that the sign-in finished. The teaching screen
    # still sends the click a person would have picked; it is ignored here, so an older panel and
    # a newer compiler cannot disagree about what a sign-in writes.
    if options.sign_in:
        options = replace(options, commit_step_ids=[], commit_assertions={})

    candidates, candidate_warnings = to_candidates(events, options)
    warnings.extend(candidate_warnings)
    _refuse_what_cannot_replay(events, candidates)
    candidates, chip_warnings = _fold_chip_fills(candidates)
    warnings.extend(chip_warnings)
    candidates, option_warnings = _fold_option_fills(candidates)
    warnings.extend(option_warnings)
    candidates, select_click_warnings = _fold_select_control_clicks(candidates)
    warnings.extend(select_click_warnings)
    candidates, container_warnings = _drop_container_clicks(candidates)
    warnings.extend(container_warnings)
    candidates, retried_warnings = _fold_retried_fills(candidates)
    warnings.extend(retried_warnings)
    candidates, box_warnings = _settle_suggestion_boxes(candidates)
    warnings.extend(box_warnings)
    if not candidates:
        raise CompileError("no steps could be built from the recording")

    recording_urls = [event.url for event in events if event.url]

    parameterized: list[Candidate] = []
    for candidate in candidates:
        updated, param_warnings = parameterize_candidate(candidate, options.sample_row)
        warnings.extend(param_warnings)
        parameterized.append(updated)

    parameterized = _expand_create_option(parameterized)

    # PL-123: the count on a smart button and the container a click landed on are the page's
    # shape, not the task. Strip the count, and aim a container click at the row's own value.
    warnings.extend(_strip_changeable_counts(parameterized))
    _rewrite_container_row_clicks(parameterized)
    in_place_shape = _in_place_save_shape(parameterized)

    # PL-118 item 2: "The app saves by itself when I leave the page" has no recorded Save click,
    # so its commit is added (or the click that re-opens the item is marked) before the compiler
    # looks for the search. A task that cannot be checked says so and parks every row.
    # PL-123: a *new* form the app saved in place (``.../new`` became ``.../<id>``) gets an
    # explicit save and a read-back, because a run cannot rely on hiding the window.
    self_save_checked = True
    if options.save_by_itself:
        for candidate in parameterized:
            candidate.commit = False
        self_save_checked = _in_place_save_commit(
            parameterized, options, warnings, shape=in_place_shape
        )
        parameterized, tab_warnings = _drop_tab_clicks(parameterized)
        warnings.extend(tab_warnings)
        if not self_save_checked:
            self_save_checked = _create_leave_commit(parameterized, options, warnings) or (
                _self_save_commit(parameterized, options, warnings)
            )
    else:
        parameterized, tab_warnings = _drop_tab_clicks(parameterized)
        warnings.extend(tab_warnings)
        if in_place_shape is not None and not any(candidate.commit for candidate in parameterized):
            self_save_checked = _in_place_save_commit(
                parameterized, options, warnings, shape=in_place_shape
            )

    # PL-123: a create's email duplicate check is a search before New (PL-017's rule, applied to
    # a form that adds a person who may already be registered). It belongs to the shape the app
    # saved in place, where a person can already be on the list by hand.
    extra_before = (
        _duplicate_search_bodies(parameterized, options, warnings)
        if in_place_shape is not None
        else {}
    )

    # PL-017: a search for a record's value is a fill followed by the step that runs it. A search
    # button submits its own form, so the recorder collapses it into a commit; it changes nothing in
    # the destination, so it is demoted before any assertion is required of it, and the branch that
    # reads its result goes straight after it.
    #
    # The search detector reads the *unparameterized* candidates (the search fill is a literal
    # there), so a self-save recording needs a commit in that list too: the recording does save,
    # even though the person chose no click. The last step stands in for the commit the compiler
    # just added; it is a copy, so the real candidates are untouched.
    search_source = candidates
    if options.save_by_itself and candidates:
        search_source = [replace(candidate) for candidate in candidates]
        search_source[-1].commit = True
    search = detect_duplicate_search(search_source, options)
    # PL-077: which searched shape this is, decided from the recording rather than from whether the
    # person ticked the rule -- the form has to word its checkbox even when it is unticked.
    shape_search = search or detect_duplicate_search(
        search_source, replace(options, duplicate_check=True)
    )
    search_kind = (
        "update"
        if shape_search is not None and detect_find_then_update(search_source, shape_search[0])
        else "create"
        if shape_search is not None
        else None
    )

    # PL-079: an update is keyed by the values it writes, so a later change to the same record is
    # new work. The columns come from the parameterized fills, so only a column the sample row
    # carries can enter the key; a create's key is the record key, unchanged.
    update_columns = (
        _update_columns(parameterized, shape_search[0])
        if search_kind == "update" and shape_search is not None
        else []
    )

    meta = _resolve_meta(
        parameterized, recording_urls, options, warnings, update_columns=update_columns
    )

    search_branch: dict[str, Any] | None = None
    if search is not None:
        search_run, searched_column = search
        parameterized[search_run].commit = False
        warnings.append(
            "the search step is not a commit step; it reads the destination, it does not change it"
        )
        value_template = f"{{{{row.{searched_column} | lower}}}}"
        if search_kind == "update":
            # The search finds the record to change: skipping it would skip every row, and finding
            # nothing leaves nothing to change, so that row is parked with the column and value
            # named -- a person decides whether to skip it (PL-106 review; PL-113 item 6 restored
            # the park at T1's review, keeping the plain sentence).
            search_branch = _not_found_branch(searched_column, value_template)
            warnings.append(
                "the search finds the record to change; if it finds nothing, the row is left for "
                "you to skip"
            )
        else:
            search_branch = _duplicate_branch(value_template)
    else:
        searched_column = ""

    # PL-112: a choice that names a step the folds removed would silently compile a task that never
    # saves. The person is told instead, in the same warnings list every other fold reports through.
    if options.commit_step_ids:
        surviving = {candidate.recorded_id for candidate in parameterized}
        if any(step_id not in surviving for step_id in options.commit_step_ids):
            warnings.append(
                "the click chosen to save your work is not in the steps this recording compiles "
                "to, so nothing will be saved; record it again and pick that click"
            )

    without_assertion: list[str] = []
    chrome_assertions: list[str] = []
    chrome_names = _chrome_names(parameterized)
    for index, candidate in enumerate(parameterized):
        if not candidate.commit:
            continue
        supplied = (options.commit_assertions or {}).get(candidate.recorded_id or "")
        sample_values = options.sample_row.values()
        observation_pins = (
            condition_from_observation(
                candidate.observation,
                options.assertion_timeout_ms,
                sample_values=sample_values,
            )
            is not None
        )
        inferred_read_back = False
        if candidate.assert_after is not None:
            # PL-123: the compiler's own commit (a saves-by-itself re-open, or an explicit save
            # for a form the app saved in place) carries the check it built. PL-118: that check is
            # a read-back, and it is proof because the page was loaded fresh. On a Save click the
            # field already shows the value the run typed when the freshness probe runs, so the
            # same check would answer "present" and park every row (review B1).
            assert_after = candidate.assert_after
            inferred_read_back = True
        else:
            assert_after = _resolve_commit_assertion(candidate, options)
        inferred_from_new_page = False
        if assert_after is None and not candidate.after_url and candidate.action == "click":
            # PL-059: the save was sent from a "new" page and the person saw no navigation, so
            # the address it must land on is the evidence. Unlike "the name appears again", it
            # cannot be true on the form the save was sent from: /odoo/sales/new does not match
            # /odoo/sales/<id>. Only a click is a save; a select or a press commits nothing.
            assert_after = condition_from_new_page(candidate.url, options.assertion_timeout_ms)
            inferred_from_new_page = assert_after is not None
        inferred_from_fill = False
        if assert_after is None:
            fallback = candidate
            if observation_is_generated_reference(candidate.observation, sample_values):
                # The number belongs to the demonstration's record, so it is no evidence for
                # this one. The next assertion the compiler would pick -- the new record's
                # name appearing -- is the real check (PL-049).
                fallback = replace(candidate, observation=None)
            assert_after = _infer_assertion_from_fills(fallback, parameterized[:index], options)
            if assert_after is not None:
                inferred_from_fill = True
                warnings.append("this save is checked by the new record's name appearing")
        candidate.assert_after = assert_after
        frame_text = _is_chrome_assertion(candidate.assert_after, chrome_names)
        if frame_text is not None:
            chrome_assertions.append(
                f"commit step {candidate.recorded_id or candidate.seq} is checked by "
                f"“{frame_text}”, which is part of the page's own frame (a tab, a link or a "
                "menu): it is there whether or not the change was saved, so it cannot prove one; "
                "re-record the save with a check that shows the change, or type the new value "
                "and leave the field"
            )
            continue
        if candidate.assert_after is None:
            without_assertion.append(
                f"commit step {candidate.recorded_id or candidate.seq} has no assert_after: "
                "the recording observed no confirmation and no navigation, and none was "
                "supplied for it"
            )
        elif inferred_read_back:
            warnings.append("this save is checked by reading the changed value back from the page")
        elif supplied:
            warnings.append(
                f"assertion for {candidate.recorded_id or candidate.seq} "
                "taken from the one supplied for it"
            )
        elif observation_pins:
            warnings.append(
                f"assertion for {candidate.recorded_id or candidate.seq} "
                "taken from the recorded observation"
            )
        elif inferred_from_new_page:
            warnings.append("this save is checked by the page moving from /new to the saved record")
        elif inferred_from_fill:
            warnings.append(
                f"assertion for {candidate.recorded_id or candidate.seq} "
                "inferred from a previous fill of the record key"
            )
        else:
            warnings.append(
                f"assertion for {candidate.recorded_id or candidate.seq} "
                "inferred from the navigation the recording observed"
            )
            # A page-pattern assertion is only evidence when the page the step ran on does not
            # already satisfy it. When it does -- two record ids under one path, say -- say so:
            # a person reviewing the task should know this commit is proved by "the page moved",
            # not by "the change landed".
            pattern = str(candidate.assert_after.get("url_matches", ""))
            if (
                pattern
                and candidate.url
                and re.search(pattern, candidate.url)
                # PL-075: `element_absent` is the proof; the URL only pins the record page, so an
                # update-in-place is not the weakness this warning is about.
                and "element_absent" not in candidate.assert_after
            ):
                warnings.append(
                    f"assertion for {candidate.recorded_id or candidate.seq} is also true on the "
                    f"page the step ran on ({pattern!r}), so it cannot tell an accepted commit "
                    "from a refused one; re-record a step whose confirmation is visible"
                )
    if chrome_assertions:
        raise CompileError(
            "a commit step is checked by part of the page's own frame, so nothing proves the save",
            problems=chrome_assertions,
        )
    if without_assertion:
        raise CompileError(
            "a commit step must carry an assertion, and none could be inferred",
            problems=without_assertion,
        )

    _check_navigate_origins(parameterized, meta["allowed_origins"])
    # PL-079: an update whose Save disappears is the one shape where "the control is absent before
    # the click" means "nothing to save", so the recipe ends that row ``skipped`` itself.
    already_branch_before, already_branch = _already_matches_placement(parameterized, search_kind)
    recipe: dict[str, Any] = {
        "format_version": 1,
        "meta": meta,
        "steps": _render_steps(
            parameterized,
            options,
            duplicate_branch_after=search[0] if search is not None else None,
            duplicate_branch=search_branch,
            already_branch_before=already_branch_before,
            already_branch=already_branch,
            extra_before=extra_before,
        ),
    }

    # PL-113 S2: a sign-in is not finished by clicking Continue. The run waits (up to 90 s, so the
    # code email has time to arrive) for the page the demonstration reached after the code page,
    # and the record is Done only when that page is reached.
    if options.sign_in:
        landing = sign_in_landing_pattern(options.sign_in_after_path or "")
        if landing is None:
            warnings.append(
                "this task is a sign-in, but the recording couldn't see the page after the code, "
                "so nothing checks that the sign-in finished"
            )
        else:
            recipe["steps"].append(
                {
                    "id": "sign-in-finished",
                    "action": "wait_for",
                    "name": "Wait until the sign-in finishes",
                    "intent": "wait until the sign-in finishes",
                    "condition": {"url_matches": landing, "timeout_ms": SIGN_IN_WAIT_MS},
                    "timeout_ms": SIGN_IN_WAIT_MS,
                }
            )

    # PL-118 item 1: no read-back means no row can ever be Done. The compiled task still shows
    # the steps for review, but its last step parks every record with the ticket's sentence.
    if options.save_by_itself and not self_save_checked:
        recipe["steps"].append(
            {
                "id": "end-record-unchecked",
                "action": "end_record",
                "intent": "stop this row because the save could not be checked",
                "outcome": "parked",
                "reason": READ_BACK_UNKNOWN_REASON,
            }
        )

    _fit_step_names(recipe.get("steps"))
    try:
        assert_valid_recipe(recipe)
    except CompileError as exc:
        # PL-103: the step a refusal is about is named first, in words a person can act on.
        raise CompileError(
            "the compiled recipe does not match the recipe schema",
            problems=plain_step_problems(recipe, exc.problems),
        ) from exc
    return CompileResult(recipe=recipe, warnings=warnings, search_kind=search_kind)


# PL-103: the recipe schema's limit on a step name.
STEP_NAME_MAX = 120

_STEP_PATH = re.compile(r"^steps/(\d+)(?:/|:)")


def _fit_step_names(steps: Any) -> None:
    """Cut every step name, nested branches included, to the schema's 120 characters.

    A name is only a label; one made of a page's text must not cost the person the whole save.
    """
    if isinstance(steps, list):
        for step in steps:
            _fit_step_names(step)
        return
    if not isinstance(steps, dict):
        return
    name = steps.get("name")
    if isinstance(name, str) and len(name) > STEP_NAME_MAX:
        steps["name"] = name[: STEP_NAME_MAX - 1].rstrip() + "…"
    for value in steps.values():
        if isinstance(value, (list, dict)):
            _fit_step_names(value)


def plain_step_problems(recipe: Mapping[str, Any], problems: list[str]) -> list[str]:
    """The schema's problems, each refused step named first in one plain sentence.

    The engine's own lines are kept after them for support. A problem outside the steps is left as
    it is.
    """
    steps = recipe.get("steps")
    named: list[str] = []
    seen: set[int] = set()
    for problem in problems:
        match = _STEP_PATH.match(problem)
        if match is None:
            continue
        index = int(match.group(1))
        if index in seen:
            continue
        seen.add(index)
        step = steps[index] if isinstance(steps, list) and index < len(steps) else {}
        name = str(step.get("name") or "") if isinstance(step, dict) else ""
        label = f' ("{name[:60]}")' if name else ""
        named.append(
            f"Step {index + 1}{label} can't be saved as it was recorded. "
            "Remove it with × and save again."
        )
    return named + list(problems)
