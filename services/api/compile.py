"""Compile a recorded demonstration into a recipe, without storing anything.

The popup's "Make recipe" form sends the recording it just captured here,
together with what a recording cannot know about itself: the workflow's name and
when-to-use line, the sample row, which column is the record key, which origins
are allowed, and which recorded steps are the commit. The compiler
(``packages.compiler``) turns that into a recipe and a list of warnings, and this
route returns both, so the builder sees exactly what the recording compiled to.

Nothing is stored and no database is touched. Compiling is a pure function over
the recording; keeping the result is a separate, explicit call
(``POST /api/v1/recipes``), so a compile that is thrown away leaves no trace.

The route sits behind the front door (``services.api.middleware``), which is the
one place the extension's own origin is accepted, because the popup has no other
origin to present.

A compiler error is a 422 that lists every problem, in the same shape
``services.api.recipes`` uses when it refuses to store a recipe. The commonest is
a commit step that carries no ``assert_after``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from packages.compiler import CompileError, CompileOptions, compile_recording
from services.ai import AiService, CleanRecordingRequest

from .db import connection
from .operator import require_operator_user

router = APIRouter(prefix="/api/v1", tags=["recipes"])


class CompileRequest(BaseModel):
    """The popup's "Make recipe" body: the recording and the options around it."""

    recording: list[dict[str, Any]]
    name: str
    when_to_use: str
    sample_row: dict[str, Any] = {}
    record_key_column: str | None = None
    # PL-105: a task taught with no recorded inputs has no column to build a key from, so the
    # panel supplies the key template itself (`{{row.run_once_id}}`).
    record_key: str | None = None
    allowed_origins: list[str] | None = None
    # PL-112: ``None`` means the caller predates the field (the compiler keeps its earlier rule);
    # ``[]`` means the person chose "Nothing gets saved" and no step may commit.
    commit_step_ids: list[str] | None = None
    # PL-017 built-in rules. ``duplicate_check`` is on by default; ``park_fields`` are the columns
    # the person marked required in the Make recipe form.
    duplicate_check: bool = True
    park_fields: list[str] = []
    # PL-113 S2: this demonstration signed in, and the page it reached after the code page. A
    # sign-in recipe ends by waiting for that page; without one the recipe says so in its warnings.
    sign_in: bool = False
    sign_in_after_path: str | None = None
    # PL-118: the person chose "The app saves by itself when I leave the page". The compiler
    # builds the leave/re-open commit and its value read-back; no click is sent as the save.
    save_by_itself: bool = False
    # PL-123: teaching's one-sentence intent per recorded step id, from the panel's Stop call.
    intents: dict[str, str] = {}


class CompileResponse(BaseModel):
    """The compiled recipe and everything the builder should know about it."""

    recipe: dict[str, Any]
    warnings: list[str]
    # PL-077: which searched shape this recording is. "update" is a find-then-update (the search
    # finds the record to change), "create" is everything else, and null means no search at all.
    # The Make recipe form words its search rule from this, so the person reads the rule the
    # compiler actually applied.
    search_kind: str | None = None


@router.post("/recipes/compile", response_model=CompileResponse)
def compile_recipe(request: CompileRequest) -> CompileResponse:
    """Compile a recording into a recipe. Stores nothing, touches no database.

    Fail-closed: a recording the compiler cannot turn into a recipe that matches
    the schema is refused with every problem listed, and the builder is told what
    to re-record or supply.
    """
    options = CompileOptions(
        name=request.name,
        when_to_use=request.when_to_use,
        sample_row=request.sample_row,
        record_key_column=request.record_key_column,
        record_key=request.record_key,
        allowed_origins=request.allowed_origins,
        commit_step_ids=request.commit_step_ids,
        duplicate_check=request.duplicate_check,
        park_fields=request.park_fields,
        sign_in=request.sign_in,
        sign_in_after_path=request.sign_in_after_path,
        save_by_itself=request.save_by_itself,
        intents=request.intents,
    )
    try:
        result = compile_recording(request.recording, options)
    except CompileError as exc:
        # ``CompileError`` appends its problems to the message, one per line, so
        # the message's first line is the headline a caller can show as-is.
        message = str(exc).split("\n", 1)[0]
        raise HTTPException(
            status_code=422,
            detail={"message": message, "problems": list(exc.problems) or [message]},
        ) from exc
    return CompileResponse(
        recipe=result.recipe, warnings=result.warnings, search_kind=result.search_kind
    )


class CleanRecordingBody(BaseModel):
    """The panel's Stop body: the recording it just captured."""

    recording: list[dict[str, Any]]


class CleanedStep(BaseModel):
    """One recorded step, kept or hidden, and the sentence it is for."""

    id: str
    keep: bool
    intent: str


class CleanRecordingResponse(BaseModel):
    """The kept steps and their intents, or today's list when no model is configured.

    ``available`` is false when teaching ran without a model: the panel then shows the recording
    unchanged, exactly as it did before PL-123.
    """

    available: bool
    steps: list[CleanedStep]
    reason: str | None = None


def _recorded_steps(recording: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The steps a recording carries, in the shape ``clean_recording`` validates."""
    steps: list[dict[str, Any]] = []
    for raw in recording:
        if not isinstance(raw, dict):
            continue
        step = raw.get("step")
        if not isinstance(step, dict):
            continue
        step_id = step.get("id")
        action = step.get("action")
        if not isinstance(step_id, str) or not isinstance(action, str):
            continue
        if action not in ("fill", "select", "click", "check", "press"):
            continue
        prepared: dict[str, Any] = {"id": step_id, "action": action}
        # PL-124: who made the step is the one fact the model needs to tell the page's own value
        # from the person's. Without it the owner's typed price looked the same as the price the
        # page showed, and the model kept the page's. Only the origin travels, never the value.
        prepared["by"] = "page" if raw.get("page_set") is True else "person"
        if isinstance(step.get("name"), str):
            prepared["name"] = step["name"]
        if isinstance(step.get("targets"), list):
            prepared["targets"] = step["targets"]
        steps.append(prepared)
    return steps


@router.post("/recordings/clean", response_model=CleanRecordingResponse)
def clean_recording(body: CleanRecordingBody) -> CleanRecordingResponse:
    """Ask teaching to drop the page's own steps and name the rest (PL-123 item 1).

    With no model, or an answer the gate refuses, the recording is returned unchanged: the panel
    shows today's list and the person can still save it. A dropped step is only ever a drop; the
    code gate in ``services/ai`` refuses an answer that adds a step or changes a value.
    """
    steps = _recorded_steps(body.recording)
    if not steps:
        return CleanRecordingResponse(available=False, steps=[])
    with connection() as conn:
        user = require_operator_user(conn)
        service = AiService.from_env(conn)
        if not service.available:
            return CleanRecordingResponse(
                available=False,
                steps=[CleanedStep(id=step["id"], keep=True, intent="") for step in steps],
                reason="no model is configured, so the recording is shown unchanged",
            )
        result = service.clean_recording(
            CleanRecordingRequest(tenant_id=user.tenant_id, steps=tuple(steps))
        )
        # The `ai_calls` row is the audit trail; the request's connection must commit it before
        # it closes, or a refused answer would vanish from the log.
        conn.commit()
    if result.status != "ok":
        return CleanRecordingResponse(
            available=False,
            steps=[CleanedStep(id=step["id"], keep=True, intent="") for step in steps],
            reason=result.reason or "the model did not answer",
        )
    return CleanRecordingResponse(
        available=True,
        steps=[
            CleanedStep(id=step.id, keep=step.keep, intent=step.intent) for step in result.steps
        ],
    )
