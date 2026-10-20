"""The ProcessLens HTTP API.

Every route answers only on the local interface, behind the front door in ``middleware``. The
health endpoint reports two things: that this process is up, and whether it can reach the database.
The operator endpoint reports the one local operator id the page acts as.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import psycopg
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from .admin import router as admin_router
from .approvals import router as approvals_router
from .apps import router as apps_router
from .batches import router as batches_router
from .compile import router as compile_router
from .documents import router as documents_router
from .executor import router as executor_router
from .home import router as home_router
from .hub import router as hub_router
from .middleware import FrontDoorMiddleware
from .operator import operator_id
from .recipes import router as recipes_router
from .runs import router as runs_router
from .spreadsheets import router as spreadsheets_router
from .tasks import router as tasks_router
from .workspace import router as workspace_router

logger = logging.getLogger("processlens.api")

CONNECT_TIMEOUT_SECONDS = 2
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
app.include_router(tasks_router)
app.include_router(compile_router)
app.include_router(batches_router)
app.include_router(documents_router)
app.include_router(runs_router)
app.include_router(approvals_router)
app.include_router(admin_router)
app.include_router(executor_router)
app.include_router(workspace_router)
app.include_router(hub_router)
app.include_router(home_router)
app.include_router(apps_router)
app.include_router(spreadsheets_router)


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
    return OperatorResponse(operator_id=operator_id())


# The page and the API share one origin, so the page fetches relative paths and the front door's
# own-origin rule holds with no CORS header anywhere. Registered last, on purpose: a mount at ``/``
# catches whatever no earlier route matched, so it must come after every route above it -- the
# routers, ``/health`` and ``/api/v1/operator`` alike. When the page has not been built there is
# nothing to mount, and ``/`` answers one line telling the operator how to build it rather than
# crashing at import or serving a directory that is not there.
class SpaStaticFiles(StaticFiles):
    """The built page, with one rule: a client-side route is not a missing file.

    A person can refresh on ``/runs/abc``. No such file exists, so the mount would answer 404 and
    the page would never load. A path whose suffix is not a known asset suffix therefore answers
    ``index.html`` and the router inside the page decides what to draw. Suffixes are decided by an
    allowlist, not by "has a dot at all": a route like ``/runs/abc.def`` is a route, not a missing
    asset, and it must survive a refresh.

    Two things still 404 rather than answering the page, on purpose:

    * a missing *asset* -- a path whose suffix is one of ``ASSET_SUFFIXES`` -- so a broken build is
      visible instead of a page that quietly serves a stylesheet that is not there;
    * anything under ``/api/``, so a mistyped API path stays the JSON 404 the API itself would
      send. Handing the page's HTML to an API caller would turn a plain 404 into a 200 whose body
      no caller can parse.
    """

    # The prefix the API owns; a miss under it is the API's 404, never the page.
    API_PREFIX = "api/"

    # The suffixes a real built asset can carry. Anything else, dot included, is a route.
    ASSET_SUFFIXES = {
        ".js",
        ".css",
        ".map",
        ".png",
        ".jpg",
        ".jpeg",
        ".svg",
        ".ico",
        ".webp",
        ".woff",
        ".woff2",
        ".txt",
        ".json",
    }

    async def get_response(self, path: str, scope: MutableMapping[str, Any]) -> Response:
        """Answer the file, or the page for a route, or 404 for a missing asset or API path.

        ``path`` has already been through ``os.path.normpath``, so on Windows its separators are
        backslashes; they are normalised back before the prefix test, or ``/api/...`` would slip
        past it on this machine and not on the deployment.
        """
        relative = path.replace("\\", "/").lstrip("/")
        is_api = relative == self.API_PREFIX.rstrip("/") or relative.startswith(self.API_PREFIX)
        is_asset = Path(relative).suffix.lower() in self.ASSET_SUFFIXES
        if is_api or is_asset:
            return await super().get_response(path, scope)
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404:
            return await super().get_response("index.html", scope)
        return response


if WEB_DIST.is_dir():
    app.mount("/", SpaStaticFiles(directory=WEB_DIST, html=True), name="web")
else:

    @app.get("/", response_class=PlainTextResponse, include_in_schema=False)
    def page_not_built() -> str:
        """One line, not a traceback, when the operator page has no build yet."""
        return BUILD_HINT
