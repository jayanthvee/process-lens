"""Database access for the API.

One connection per request, built from ``DATABASE_URL``. When the variable is
missing or the server is unreachable the endpoint answers 503 rather than 500,
because "the database is not there" is a dependency condition, not a bug in the
request.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from fastapi import HTTPException

CONNECT_TIMEOUT_SECONDS = 5


def database_url() -> str:
    """The configured connection string, or an empty string."""
    return os.environ.get("DATABASE_URL", "").strip()


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """A connection for one request, closed when the block ends.

    Raises :class:`HTTPException` 503 when the database is not configured or
    cannot be reached, so every caller gets the same answer for the same cause.
    """
    dsn = database_url()
    if not dsn:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    try:
        conn = psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    except psycopg.OperationalError as exc:
        raise HTTPException(status_code=503, detail="the database is unreachable") from exc
    try:
        yield conn
    finally:
        conn.close()
