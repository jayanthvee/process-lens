"""The ProcessLens HTTP API.

Every route answers only on the local interface, behind the front door in ``middleware``. The
health endpoint reports two things: that this process is up, and whether it can reach the database.
The operator endpoint reports the one local operator id the page acts as.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .approvals import router as approvals_router
from .hub import router as hub_router
from .middleware import FrontDoorMiddleware
from .recipes import router as recipes_router
from .runs import router as runs_router

logger = logging.getLogger("processlens.api")

CONNECT_TIMEOUT_SECONDS = 2
OPERATOR_ENV_VAR = "PROCESSLENS_OPERATOR_ID"
WEB_DIST = Path(__file__).resolve().parents[2] / "apps" / "web" / "dist"
BUILD_HINT = (
    "the operator page is not built yet; run: npm --prefix apps/web install "
    "&& npm --prefix apps/web run build"
)

app = FastAPI(title="ProcessLens API", version="0.1.0")
# The front door covers the routers included below, and every route added after it. The hub routes
# keep their own, stricter checks on top.
app.add_middleware(FrontDoorMiddleware)
app.include_router(recipes_router)
app.include_router(runs_router)
app.include_router(approvals_router)
app.include_router(hub_router)


# NOTE: the page's static mount is added at the very END of this module, after every route is
# registered, because a mount at ``/`` catches anything no earlier route matched.


def database_available() -> bool:
    """True when ``DATABASE_URL`` can be reached and answers a query."""
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return False
    try:
        with (
            psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute("SELECT 1")
            cur.fetchone()
    except psycopg.Error as exc:
        logger.warning("database health check failed: %s", exc)
        return False
    return True


@app.get("/health")
def health() -> dict[str, object]:
    """Liveness plus database reachability.

    ``status`` describes this process; ``db`` describes the dependency. The
    response shape is the same either way, so a caller can read both fields.
    """
    return {"status": "ok", "db": database_available()}


class OperatorResponse(BaseModel):
    """The identity the page sends as ``decided_by`` or ``destination_checked_by``."""

    operator_id: uuid.UUID


@app.get("/api/v1/operator", response_model=OperatorResponse, tags=["operator"])
def operator() -> OperatorResponse:
    """The one local operator id, read from ``PROCESSLENS_OPERATOR_ID``.

    There are no users yet, so the id is configuration. When it is unset, blank or not a UUID the
    answer is 503 with a sentence the page can show, and the page disables every action that needs
    a person's id. The API never invents one.
    """
    raw = os.environ.get(OPERATOR_ENV_VAR, "").strip()
    if not raw:
        raise HTTPException(
            status_code=503,
            detail=f"{OPERATOR_ENV_VAR} is not set, so there is no operator id to act as",
        )
    try:
        return OperatorResponse(operator_id=uuid.UUID(raw))
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"{OPERATOR_ENV_VAR} is not a UUID",
        ) from exc


# The page and the API share one origin, so the page fetches relative paths and the front door's
# own-origin rule holds with no CORS header anywhere. Registered last, on purpose: a mount at ``/``
# catches whatever no earlier route matched, so it must come after every route above it -- the
# routers, ``/health`` and ``/api/v1/operator`` alike. When the page has not been built there is
# nothing to mount, and ``/`` answers one line telling the operator how to build it rather than
# crashing at import or serving a directory that is not there.
if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
else:

    @app.get("/", response_class=PlainTextResponse, include_in_schema=False)
    def page_not_built() -> str:
        """One line, not a traceback, when the operator page has no build yet."""
        return BUILD_HINT
