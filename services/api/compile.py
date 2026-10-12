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

router = APIRouter(prefix="/api/v1", tags=["recipes"])


class CompileRequest(BaseModel):
    """The popup's "Make recipe" body: the recording and the options around it."""

    recording: list[dict[str, Any]]
    name: str
    when_to_use: str
    sample_row: dict[str, Any] = {}
    record_key_column: str | None = None
    allowed_origins: list[str] | None = None
    commit_step_ids: list[str] = []


class CompileResponse(BaseModel):
    """The compiled recipe and everything the builder should know about it."""

    recipe: dict[str, Any]
    warnings: list[str]


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
        allowed_origins=request.allowed_origins,
        commit_step_ids=request.commit_step_ids,
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
    return CompileResponse(recipe=result.recipe, warnings=result.warnings)
