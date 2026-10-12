"""Database fixtures and the local test client for ``tests/api``.

The pattern is ``tests/db/conftest.py``'s: one database is created for the whole session, every
migration in ``db/migrations`` is applied to it, and it is dropped at the end.

Every client here is a *local* one -- ``base_url`` names ``127.0.0.1:8000`` and the peer address is
loopback -- because the API's front door (``services/api/middleware.py``) refuses anything else,
exactly as the hub's own front door does. A test that needs a refusal builds its own client with a
different peer or ``Host``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from db.migrate import apply_migrations, read_dotenv
from services.api.main import app
from tests.db.seed import Ledger

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"

LOCAL_BASE_URL = "http://127.0.0.1:8000"
LOCAL_CLIENT = ("127.0.0.1", 51234)


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
    name = f"processlens_api_test_{uuid.uuid4().hex[:10]}"
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
def client() -> TestClient:
    """The front door's own client: a loopback peer asking for ``127.0.0.1:8000``."""
    return TestClient(app, base_url=LOCAL_BASE_URL, client=LOCAL_CLIENT)


@pytest.fixture
def connected(monkeypatch: MonkeyPatch, database_url: str, client: TestClient) -> TestClient:
    """The local client, pointed at this directory's database."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    return client
