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
from .coalesce import coalesce_fills, collapse_presses, collapse_submits
from .errors import CompileError
from .model import ACTIONABLE_KINDS, STEP_ACTIONS, Candidate, Event, target_signature
from .normalize import NAVIGATE_EVENT_KIND, UNRECORDABLE_EVENT_KIND, parse_events
from .parameterize import parameterize_candidate
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


def to_candidates(
    events: list[Event], options: CompileOptions
) -> tuple[list[Candidate], list[str]]:
    """Read each actionable event into a candidate step, dropping empty actions."""
    commit_ids = set(options.commit_step_ids or ())
    candidates: list[Candidate] = []
    warnings: list[str] = []

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
            if accept.action == "press" or (accept.action == "select" and same_field):
                dropped.add(position)
        warnings.append(
            f"folded the fill superseded by the chip fill at event {candidate.seq} into the chip"
        )

    if not dropped:
        return list(candidates), warnings
    return [item for position, item in enumerate(candidates) if position not in dropped], warnings


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


def _looks_like_a_search(fill: Candidate, click: Candidate) -> bool:
    """Whether a fill-then-click pair reads as a search, not any other fill-then-click.

    A wizard's ``fill email -> Next`` is a fill immediately followed by a click too, and mistaking
    it for a search would turn on the duplicate branch and silently skip records. The one durable
    signal a recording carries is the control's own name: a search box or a search button says
    "search". Erring the other way is safe -- a search that is not recognised simply gets no branch
    -- so a pair with no such signal is not a search.
    """
    for candidate in (fill, click):
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


def detect_record_key_search(candidates: list[Candidate], options: CompileOptions) -> int | None:
    """The index of the click that runs a search for the record key, or None.

    A search for the record key is a ``fill`` whose recorded value is the record key column's
    sample value, immediately followed by the click that runs it -- a search control, which
    :func:`_looks_like_a_search` recognises -- with the create's own commit still to come. The
    "looks like a search" check and the later commit are what separate a search from a create that
    merely fills the key, or from a wizard's ``fill -> Next``. No search means no branch; the
    compiler never invents one.
    """
    column = options.record_key_column
    if not column:
        return None
    sample = str(options.sample_row.get(column, "")).strip()
    if not sample:
        return None
    for index, candidate in enumerate(candidates):
        if candidate.action != "fill":
            continue
        value = candidate.value
        if not (isinstance(value, dict) and value.get("kind") == "constant"):
            continue
        if str(value.get("value", "")).strip() != sample:
            continue
        if index + 1 >= len(candidates) or candidates[index + 1].action != "click":
            continue
        click = candidates[index + 1]
        if not _looks_like_a_search(candidate, click):
            continue
        # A create still commits after the search; the search itself is not the create.
        if not any(later.commit for later in candidates[index + 2 :]):
            continue
        return index + 1
    return None


def _duplicate_branch(record_key: str) -> dict[str, Any]:
    """A branch that ends a record ``skipped`` when the search already shows its key."""
    return {
        "action": "branch",
        "name": "Skip a record if the search finds it",
        "when": {"text_visible": record_key},
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
    if not candidates:
        raise CompileError("no steps could be built from the recording")

    recording_urls = [event.url for event in events if event.url]

    parameterized: list[Candidate] = []
    for candidate in candidates:
        updated, param_warnings = parameterize_candidate(candidate, options.sample_row)
        warnings.extend(param_warnings)
        parameterized.append(updated)

    meta = _resolve_meta(parameterized, recording_urls, options, warnings)

    # PL-017: a search for the record key is a fill followed by the click that runs it. The search
    # button submits its own form, so the recorder collapses it into a commit; it changes nothing in
    # the destination, so it is demoted before any assertion is required of it, and the branch that
    # reads its result goes straight after it.
    search_click = (
        detect_record_key_search(candidates, options) if options.duplicate_check else None
    )
    if search_click is not None:
        parameterized[search_click].commit = False
        warnings.append(
            "the search step is not a commit step; it reads the destination, it does not change it"
        )

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

    _check_navigate_origins(parameterized, meta["allowed_origins"])
    duplicate_branch = _duplicate_branch(meta["record_key"]) if search_click is not None else None
    recipe: dict[str, Any] = {
        "format_version": 1,
        "meta": meta,
        "steps": _render_steps(
            parameterized,
            options,
            duplicate_branch_after=search_click,
            duplicate_branch=duplicate_branch,
        ),
    }

    assert_valid_recipe(recipe)
    return CompileResult(recipe=recipe, warnings=warnings)
