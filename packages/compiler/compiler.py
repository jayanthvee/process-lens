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
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from .assertions import (
    DEFAULT_ASSERTION_TIMEOUT_MS,
    infer_assertion,
    origin_of,
    path_pattern,
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


@dataclass
class CompileResult:
    """The compiled recipe and anything the builder should know about it."""

    recipe: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


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
    """Read each actionable event into a candidate step, dropping empty actions."""
    commit_ids = set(options.commit_step_ids or ())
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
        candidates.append(
            Candidate(
                action=action,
                targets=targets,
                value=value,
                name=step.get("name") if isinstance(step.get("name"), str) else None,
                key=step.get("key") if isinstance(step.get("key"), str) else None,
                url=event.url,
                after_url=_next_actionable_url(events, index),
                seq=event.seq,
                commit=event.commit or (recorded_id is not None and recorded_id in commit_ids),
                observation=event.observation,
                recorded_id=recorded_id,
                frame=step.get("frame") if isinstance(step.get("frame"), list) else None,
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

    return {
        "name": options.name,
        "when_to_use": options.when_to_use,
        "input_schema": input_schema,
        "record_key": record_key,
        "idempotency_key": options.idempotency_key or record_key,
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


def _render_candidate(candidate: Candidate) -> dict[str, Any]:
    if candidate.action == "navigate":
        # The schema's step_navigate allows nothing but id, action, name and url.
        return {
            "action": "navigate",
            "name": candidate.name or f"Open {path_pattern(candidate.url) or candidate.url}",
            "url": candidate.url,
        }
    step: dict[str, Any] = {"action": candidate.action}
    step["name"] = candidate.name or _default_name(candidate.action, candidate.targets)
    step["targets"] = candidate.targets
    if candidate.frame:
        step["frame"] = candidate.frame
    if candidate.action == "press":
        # The schema's step_press carries no commit and no assert_after -- a press alone changes no
        # destination record -- so neither is ever emitted for one.
        step["key"] = candidate.key
        return step
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


def _drop_container_clicks(
    candidates: list[Candidate],
) -> tuple[list[Candidate], list[str]]:
    """Drop clicks on containers, not controls.

    PL-031: a click on a group or card whose text contains the labels of two or more inputs is
    not a control click. So is a bare css-only click with no role, label or test id.
    """
    warnings: list[str] = []
    kept: list[Candidate] = []
    labels = _input_labels(candidates)

    for candidate in candidates:
        if candidate.action != "click":
            kept.append(candidate)
            continue

        targets = candidate.targets or []
        # A css-only target with no durable semantic rung is a container, not a control.
        has_semantic = any(rung.get("by") in ("role", "testid", "label") for rung in targets)
        if not has_semantic:
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
    click_text = (
        (click.name or "") + " " + " ".join(_rung_text(rung) for rung in click.targets or [])
    )
    matched = [label for label in field_labels if label in click_text]
    other_labels = all_input_labels - field_labels
    return matched and not any(label in click_text for label in other_labels)


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
) -> list[dict[str, Any]]:
    bodies: list[dict[str, Any]] = []

    # A recording that already opens with a navigation carries its own first navigate step, so the
    # synthesized opening one is not added on top of it.
    opens_with_navigate = bool(candidates) and candidates[0].action == "navigate"
    if options.include_navigate and not opens_with_navigate:
        start = options.start_url or (candidates[0].url if candidates else "")
        if start:
            bodies.append(
                {"action": "navigate", "name": f"Open {path_pattern(start) or start}", "url": start}
            )

    for index, candidate in enumerate(candidates):
        bodies.append(_render_candidate(candidate))
        # PL-017: the duplicate check goes straight after the click that ran the search.
        if duplicate_branch is not None and index == duplicate_branch_after:
            bodies.append(duplicate_branch)

    counters: Counter[str] = Counter()
    steps: list[dict[str, Any]] = []
    for body in bodies:
        counters[body["action"]] += 1
        steps.append({"id": f"{body['action']}-{counters[body['action']]}", **body})
    return steps


def _looks_like_a_search(fill: Candidate, runner: Candidate) -> bool:
    """Whether a fill-then-run pair reads as a search, not any other fill-then-run.

    A wizard's ``fill email -> Next`` is a fill immediately followed by a click too, and mistaking
    it for a search would turn on the duplicate branch and silently skip records. The one durable
    signal a recording carries is the control's own name: a search box or a search button says
    "search". Erring the other way is safe -- a search that is not recognised simply gets no branch
    -- so a pair with no such signal is not a search.
    """
    for candidate in (fill, runner):
        for rung in candidate.targets or []:
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


def _infer_assertion_from_fills(
    candidate: Candidate,
    prior_candidates: list[Candidate],
    options: CompileOptions,
) -> dict[str, Any] | None:
    """Infer a commit assertion from a fill of the record key or a required column.

    PL-031: a quick-create save changes no URL and shows no message. The only evidence is the
    new record's name, so when a commit click has no observation and no navigation, and one of
    the fills before it was templated from the record key or a required column, use that column's
    visible text as the assertion. The record key is preferred.
    """
    if candidate.action != "click" or candidate.observation or candidate.after_url:
        return None
    commit_ids = set(options.commit_step_ids or ())
    if candidate.recorded_id not in commit_ids:
        return None

    required = _required_columns(options)
    record_key_column = options.record_key_column
    best_column: str | None = None

    for previous in reversed(prior_candidates):
        if previous.action != "fill" or not isinstance(previous.value, dict):
            continue
        column = previous.value.get("column")
        if not isinstance(column, str):
            continue
        if column == record_key_column:
            best_column = column
            break
        if column in required and best_column is None:
            best_column = column

    if best_column is None:
        return None

    return {
        "text_visible": f"{{{{row.{best_column}}}}}",
        "timeout_ms": options.assertion_timeout_ms,
    }


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

    candidates, candidate_warnings = to_candidates(events, options)
    warnings.extend(candidate_warnings)
    _refuse_what_cannot_replay(events, candidates)
    candidates, chip_warnings = _fold_chip_fills(candidates)
    warnings.extend(chip_warnings)
    candidates, option_warnings = _fold_option_fills(candidates)
    warnings.extend(option_warnings)
    candidates, container_warnings = _drop_container_clicks(candidates)
    warnings.extend(container_warnings)
    candidates, retried_warnings = _fold_retried_fills(candidates)
    warnings.extend(retried_warnings)
    if not candidates:
        raise CompileError("no steps could be built from the recording")

    recording_urls = [event.url for event in events if event.url]

    parameterized: list[Candidate] = []
    for candidate in candidates:
        updated, param_warnings = parameterize_candidate(candidate, options.sample_row)
        warnings.extend(param_warnings)
        parameterized.append(updated)

    parameterized = _expand_create_option(parameterized)

    meta = _resolve_meta(parameterized, recording_urls, options, warnings)

    # PL-017: a search for a record's value is a fill followed by the step that runs it. A search
    # button submits its own form, so the recorder collapses it into a commit; it changes nothing in
    # the destination, so it is demoted before any assertion is required of it, and the branch that
    # reads its result goes straight after it.
    search = detect_duplicate_search(candidates, options)
    if search is not None:
        search_run, searched_column = search
        parameterized[search_run].commit = False
        warnings.append(
            "the search step is not a commit step; it reads the destination, it does not change it"
        )
    else:
        searched_column = ""

    without_assertion: list[str] = []
    for index, candidate in enumerate(parameterized):
        if not candidate.commit:
            continue
        supplied = (options.commit_assertions or {}).get(candidate.recorded_id or "")
        assert_after = _resolve_commit_assertion(candidate, options)
        inferred_from_fill = False
        if assert_after is None:
            assert_after = _infer_assertion_from_fills(candidate, parameterized[:index], options)
            if assert_after is not None:
                inferred_from_fill = True
                warnings.append("this save is checked by the new record's name appearing")
        candidate.assert_after = assert_after
        if candidate.assert_after is None:
            without_assertion.append(
                f"commit step {candidate.recorded_id or candidate.seq} has no assert_after: "
                "the recording observed no confirmation and no navigation, and none was "
                "supplied for it"
            )
        elif supplied:
            warnings.append(
                f"assertion for {candidate.recorded_id or candidate.seq} "
                "taken from the one supplied for it"
            )
        elif candidate.observation:
            warnings.append(
                f"assertion for {candidate.recorded_id or candidate.seq} "
                "taken from the recorded observation"
            )
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
            if pattern and candidate.url and re.search(pattern, candidate.url):
                warnings.append(
                    f"assertion for {candidate.recorded_id or candidate.seq} is also true on the "
                    f"page the step ran on ({pattern!r}), so it cannot tell an accepted commit "
                    "from a refused one; re-record a step whose confirmation is visible"
                )
    if without_assertion:
        raise CompileError(
            "a commit step must carry an assertion, and none could be inferred",
            problems=without_assertion,
        )

    _check_navigate_origins(parameterized, meta["allowed_origins"])
    duplicate_branch = (
        _duplicate_branch(f"{{{{row.{searched_column} | lower}}}}") if search is not None else None
    )
    recipe: dict[str, Any] = {
        "format_version": 1,
        "meta": meta,
        "steps": _render_steps(
            parameterized,
            options,
            duplicate_branch_after=search[0] if search is not None else None,
            duplicate_branch=duplicate_branch,
        ),
    }

    assert_valid_recipe(recipe)
    return CompileResult(recipe=recipe, warnings=warnings)
