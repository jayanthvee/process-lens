"""Database fixtures.

One database is created for the whole session and every migration in
``db/migrations`` is applied to it, so the tests run against the real schema.
Tests that need a database of their own ask for ``scratch_database``.

``DATABASE_URL`` comes from the environment (CI sets it) or from ``.env``
(``make up`` fills the services it points at).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest

from db.migrate import apply_migrations, read_dotenv
from tests.db.seed import Ledger

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


def base_database_url() -> str:
    """The connection string for the server the tests should use."""
    read_dotenv(ENV_FILE)
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and run `make up`, "
            "or export DATABASE_URL."
        )
    return dsn


def with_database(dsn: str, database: str) -> str:
    """The same connection parameters, pointing at another database."""
    params = {key: str(value) for key, value in psycopg.conninfo.conninfo_to_dict(dsn).items()}
    params["dbname"] = database
    return psycopg.conninfo.make_conninfo(**params)


def create_database(base_dsn: str, name: str) -> str:
    """Create a database and return its connection string."""
    with psycopg.connect(base_dsn, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    return with_database(base_dsn, name)


def drop_database(base_dsn: str, name: str) -> None:
    """Drop a database, disconnecting anything still attached to it."""
    with psycopg.connect(base_dsn, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A freshly created, fully migrated database, dropped at the end of the session."""
    base_dsn = base_database_url()
    name = f"processlens_test_{uuid.uuid4().hex[:10]}"
    dsn = create_database(base_dsn, name)
    try:
        apply_migrations(dsn, log=lambda _message: None)
        yield dsn
    finally:
        drop_database(base_dsn, name)


@pytest.fixture
def conn(database_url: str) -> Iterator[psycopg.Connection]:
    """A connection to the test database where each statement stands alone."""
    with psycopg.connect(database_url, autocommit=True) as connection:
        yield connection


@pytest.fixture
def ledger(conn: psycopg.Connection) -> Ledger:
    """Synthetic ledger rows: a tenant, a workflow, a recipe version, and a way to add records."""
    return Ledger(conn)


@pytest.fixture
def scratch_database() -> Iterator[Callable[[str], str]]:
    """A factory that creates extra databases, dropped when the test finishes."""
    base_dsn = base_database_url()
    created: list[str] = []

    def factory(prefix: str = "processlens_scratch") -> str:
        name = f"{prefix}_{uuid.uuid4().hex[:10]}"
        dsn = create_database(base_dsn, name)
        created.append(name)
        return dsn

    yield factory

    for name in created:
        drop_database(base_dsn, name)
