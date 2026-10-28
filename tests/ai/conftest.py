"""Fixtures for the AI service tests.

One database is created for the session and every migration is applied, so ``ai_calls`` and the
tenant's ``data_policy`` are the real schema. No test reaches a provider: every service is built
with a stub model client.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from db.migrate import apply_migrations
from tests.db.conftest import base_database_url, create_database, drop_database


@pytest.fixture(scope="session")
def ai_database_url() -> Iterator[str]:
    base_dsn = base_database_url()
    name = f"processlens_ai_{uuid.uuid4().hex[:10]}"
    dsn = create_database(base_dsn, name)
    try:
        apply_migrations(dsn, log=lambda _message: None)
        yield dsn
    finally:
        drop_database(base_dsn, name)


@pytest.fixture
def conn(ai_database_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(ai_database_url, autocommit=True) as connection:
        yield connection


@pytest.fixture
def tenant_id(conn: psycopg.Connection) -> uuid.UUID:
    """A tenant whose policy lets the trial's provider see a redacted page snapshot."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (name, data_policy) VALUES "
            "('PL-122 synthetic tenant', %s) RETURNING id",
            (psycopg.types.json.Jsonb({"pii_providers": ["deepseek"], "synthetic_only": True}),),
        )
        return cur.fetchone()[0]
