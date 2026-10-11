"""The ProcessLens HTTP API.

Only the health endpoint exists at this stage. It reports two things: that this
process is up, and whether it can reach the database.
"""

from __future__ import annotations

import logging
import os

import psycopg
from fastapi import FastAPI

from .recipes import router as recipes_router

logger = logging.getLogger("processlens.api")

CONNECT_TIMEOUT_SECONDS = 2

app = FastAPI(title="ProcessLens API", version="0.1.0")
app.include_router(recipes_router)


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
