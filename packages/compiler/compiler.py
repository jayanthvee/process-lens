"""The compile pipeline: recorded events in, a validated recipe out.

The stages run in one order and each is a plain function over the last one's
output:

    parse -> drop noise -> fold keystrokes -> fold click/submit
          -> parameterize -> infer assertions -> build meta -> render
          -> validate against the schema

The result is deterministic: the same recording and options always compile to
the same recipe, so a compiled recipe can be diffed and reviewed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .assertions import (
    DEFAULT_ASSERTION_TIMEOUT_MS,
    infer_assertion,
    origin_of,
    path_pattern,
)
from .coalesce import coalesce_fills, collapse_submits
from .errors import CompileError
from .model import STEP_ACTIONS, Candidate, Event
from .normalize import parse_events
from .parameterize import parameterize_candidate
from .validation import assert_valid_recipe


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


def to_candidates(
    events: list[Event], options: CompileOptions
) -> tuple[list[Candidate], list[str]]:
    """Read each actionable event into a candidate step, dropping empty actions."""
    commit_ids = set(options.commit_step_ids or ())
    candidates: list[Candidate] = []
    warnings: list[str] = []

    for index, event in enumerate(events):
        step = event.step or {}
        action = step.get("action") or event.kind
        if action not in STEP_ACTIONS:
            warnings.append(f"skipped event {event.seq} with action {action!r}")
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
                url=event.url,
                after_url=_next_actionable_url(events, index),
                seq=event.seq,
                commit=event.commit or (recorded_id is not None and recorded_id in commit_ids),
                observation=event.observation,
                recorded_id=recorded_id,
                frame=step.get("frame") if isinstance(step.get("frame"), list) else None,
            )
        )
    return candidates, warnings


def derive_origins(candidates: list[Candidate], recording_urls: Iterable[str]) -> list[str]:
    """The origins the workflow actually acted on, de-duplicated and ordered."""
    origins = {origin for url in recording_urls if (origin := origin_of(url))}
    origins.update(origin for candidate in candidates if (origin := origin_of(candidate.url)))
    return sorted(origins)


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
        fields = {column: {"type": "string"} for column in options.sample_row}
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
    if options.input_rules:
        input_schema["rules"] = options.input_rules

    return {
        "name": options.name,
        "when_to_use": options.when_to_use,
        "input_schema": input_schema,
        "record_key": record_key,
        "idempotency_key": options.idempotency_key or record_key,
        "approval_policy": options.approval_policy or {"commit": "first_record"},
        "allowed_origins": origins,
    }


def _default_name(action: str, targets: list[dict[str, Any]]) -> str:
    for rung in targets:
        for key in ("name", "text", "value"):
            text = rung.get(key)
            if isinstance(text, str) and text:
                return f"{action.capitalize()} {text!r}"
    return f"{action.capitalize()} element"


def _render_candidate(candidate: Candidate) -> dict[str, Any]:
    step: dict[str, Any] = {"action": candidate.action}
    step["name"] = candidate.name or _default_name(candidate.action, candidate.targets)
    step["targets"] = candidate.targets
    if candidate.frame:
        step["frame"] = candidate.frame
    if candidate.action in ("fill", "select"):
        step["value"] = candidate.value
        if candidate.action == "select":
            step["option_match"] = "text"
        else:
            step["clear_first"] = True
    if candidate.commit:
        step["commit"] = True
        step["assert_after"] = candidate.assert_after
    return step


def _render_steps(candidates: list[Candidate], options: CompileOptions) -> list[dict[str, Any]]:
    bodies: list[dict[str, Any]] = []

    if options.include_navigate:
        start = options.start_url or (candidates[0].url if candidates else "")
        if start:
            bodies.append(
                {"action": "navigate", "name": f"Open {path_pattern(start) or start}", "url": start}
            )

    bodies.extend(_render_candidate(candidate) for candidate in candidates)

    counters: Counter[str] = Counter()
    steps: list[dict[str, Any]] = []
    for body in bodies:
        counters[body["action"]] += 1
        steps.append({"id": f"{body['action']}-{counters[body['action']]}", **body})
    return steps


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
    events, submit_warnings = collapse_submits(events)
    warnings.extend(submit_warnings)

    candidates, candidate_warnings = to_candidates(events, options)
    warnings.extend(candidate_warnings)
    if not candidates:
        raise CompileError("no steps could be built from the recording")

    recording_urls = [event.url for event in events if event.url]

    parameterized: list[Candidate] = []
    for candidate in candidates:
        updated, param_warnings = parameterize_candidate(candidate, options.sample_row)
        warnings.extend(param_warnings)
        parameterized.append(updated)

    without_assertion: list[str] = []
    for candidate in parameterized:
        if not candidate.commit:
            continue
        supplied = (options.commit_assertions or {}).get(candidate.recorded_id or "")
        candidate.assert_after = candidate.assert_after or _resolve_commit_assertion(
            candidate, options
        )
        if candidate.assert_after is None:
            without_assertion.append(
                f"commit step {candidate.recorded_id or candidate.seq} has no assertion: "
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
        else:
            warnings.append(
                f"assertion for {candidate.recorded_id or candidate.seq} "
                "inferred from the navigation the recording observed"
            )
    if without_assertion:
        raise CompileError(
            "a commit step must carry an assertion, and none could be inferred",
            problems=without_assertion,
        )

    meta = _resolve_meta(parameterized, recording_urls, options, warnings)
    recipe: dict[str, Any] = {
        "format_version": 1,
        "meta": meta,
        "steps": _render_steps(parameterized, options),
    }

    assert_valid_recipe(recipe)
    return CompileResult(recipe=recipe, warnings=warnings)
