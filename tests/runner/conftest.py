"""Fixtures for the runner tests.

One database is created for the whole session and every migration is applied to it, so the tests run
against the real schema. ``Seed`` lays down a tenant, a workflow, a recipe version and an input
then creates a run through the real ``create_run``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from db.migrate import apply_migrations, read_dotenv
from services.runner.creation import create_run
from services.runner.testing import FakeDestination, ScriptedRun
from services.runner.worker import DEFAULT_HEARTBEAT_TIMEOUT_SECONDS, Worker

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"

ORIGIN = "http://localhost:5173"
DESTINATION_KEY = "demo-crm"

# A deterministic create-lead recipe. `record_key` uses a column every row has, while the precheck
# rule parks a row whose email is missing (so the same batch can exercise skip, park and verify).
DEFAULT_STEPS: list[dict[str, Any]] = [
    {"id": "open", "action": "navigate", "url": f"{ORIGIN}/leads/new"},
    {
        "id": "fill-email",
        "action": "fill",
        "targets": [{"by": "label", "text": "Email"}],
        "value": {"kind": "input", "column": "email"},
    },
    {
        "id": "save",
        "action": "click",
        "commit": True,
        "assert_after": {"text_visible": "Lead created", "timeout_ms": 8000},
        "targets": [{"by": "role", "role": "button", "name": "Save"}],
    },
    {
        "id": "done",
        "action": "end_record",
        "outcome": "verified",
        "reason": "Lead created for {{row.key}}.",
    },
]

DEFAULT_RULES: list[dict[str, Any]] = [
    {"when": {"missing": "email"}, "then": "park", "reason": "No email address for this row."}
]


def base_database_url() -> str:
    read_dotenv(ENV_FILE)
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set (see .env)")
    return dsn


def _with_database(dsn: str, database: str) -> str:
    params = {key: str(value) for key, value in psycopg.conninfo.conninfo_to_dict(dsn).items()}
    params["dbname"] = database
    return psycopg.conninfo.make_conninfo(**params)


def _drop_database(base_dsn: str, name: str) -> None:
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
    name = f"processlens_runner_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(base_dsn, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    dsn = _with_database(base_dsn, name)
    try:
        apply_migrations(dsn, log=lambda _message: None)
        yield dsn
    finally:
        _drop_database(base_dsn, name)


@pytest.fixture(autouse=True)
def _retire_leftover_runs(conn: psycopg.Connection) -> Iterator[None]:
    """Retire any run another test left open.

    ``claim_run`` takes the oldest queued run in the database, and every test in this directory
    shares one session database, so a run left ``queued``/``running``/``paused`` by an earlier test
    would be claimed instead of the one under test. Retiring them first keeps each test order-free.
    """
    conn.execute(
        "UPDATE runs SET status = 'cancelled', finished_at = now() "
        "WHERE status IN ('queued', 'running', 'paused')"
    )
    yield


@pytest.fixture
def conn(database_url: str) -> Iterator[psycopg.Connection]:
    """A connection where each statement stands alone."""
    with psycopg.connect(database_url, autocommit=True) as connection:
        yield connection


def recipe(
    steps: list[dict[str, Any]] | None = None,
    *,
    approval_policy: dict[str, Any] | None = None,
    rules: list[dict[str, Any]] | None = None,
    record_key: str = "{{row.key}}",
    idempotency_key: str = "{{row.key}}",
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A runnable recipe document; steps default to the create-lead flow."""
    document: dict[str, Any] = {
        "format_version": 1,
        "meta": {
            "name": "Create a lead",
            "when_to_use": "Each row must exist once in the CRM.",
            "input_schema": {
                "fields": {
                    "key": {"type": "string", "required": True},
                    "email": {"type": "string"},
                },
                "rules": rules if rules is not None else DEFAULT_RULES,
            },
            "record_key": record_key,
            "idempotency_key": idempotency_key,
            "approval_policy": approval_policy or {"commit": "none"},
            "allowed_origins": [ORIGIN],
        },
        "steps": steps if steps is not None else DEFAULT_STEPS,
    }
    if variables is not None:
        document["variables"] = variables
    return document


class Seed:
    """Lays down the rows a runner test needs, using database-generated ids."""

    def __init__(self, conn: psycopg.Connection, document: dict[str, Any]) -> None:
        self.conn = conn
        self.document = document
        self._row = 0
        self.tenant_id: uuid.UUID = self._one(
            "INSERT INTO tenants (name) VALUES ('Synthetic tenant') RETURNING id"
        )
        self.workflow_id: uuid.UUID = self._one(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, 'Inquiries', %s, %s) RETURNING id",
            (self.tenant_id, DESTINATION_KEY, [ORIGIN]),
        )
        self.recipe_version_id: uuid.UUID = self._one(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, 1, %s, 'compiled from recording') RETURNING id",
            (self.tenant_id, self.workflow_id, psycopg.types.json.Jsonb(document)),
        )
        self.batch_id: uuid.UUID = self._one(
            "INSERT INTO input_batches (tenant_id, workflow_id, kind) "
            "VALUES (%s, %s, 'csv') RETURNING id",
            (self.tenant_id, self.workflow_id),
        )

    def _one(self, sql: str, params: tuple = ()) -> Any:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]

    def add_input_record(
        self,
        *,
        key: str | None = None,
        email: str | None = None,
        status: str = "ready",
        issues: list[Any] | None = None,
        **extra: Any,
    ) -> uuid.UUID:
        """One input row. ``key`` defaults to ``row-000N``."""
        self._row += 1
        key = key or f"row-{self._row:04d}"
        clean: dict[str, Any] = {"key": key, **extra}
        if email is not None:
            clean["email"] = email
        raw = dict(clean)
        return self._one(
            "INSERT INTO input_records (tenant_id, batch_id, row_index, raw, clean, record_key, "
            " status, issues) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                self.tenant_id,
                self.batch_id,
                self._row,
                psycopg.types.json.Jsonb(raw),
                psycopg.types.json.Jsonb(clean),
                key if status == "ready" else None,
                status,
                psycopg.types.json.Jsonb(issues or []),
            ),
        )

    def new_version(self, document: dict[str, Any]) -> uuid.UUID:
        """Store another immutable recipe version for this workflow and return its id."""
        return self._one(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, (SELECT coalesce(max(version), 0) + 1 FROM recipe_versions "
            " WHERE workflow_id = %s), %s, 'test') RETURNING id",
            (
                self.tenant_id,
                self.workflow_id,
                self.workflow_id,
                psycopg.types.json.Jsonb(document),
            ),
        )

    def create_run(self, *, mode: str = "batch", browser_id: str | None = None) -> uuid.UUID:
        """Create a run through the real ``create_run``, pinned to this seed's latest version."""
        with self.conn.transaction():
            return create_run(
                self.conn,
                workflow_id=self.workflow_id,
                recipe_version_id=self.recipe_version_id,
                input_batch_id=self.batch_id,
                mode=mode,
                browser_id=browser_id,
            )

    def record_ids(self, run_id: uuid.UUID) -> list[uuid.UUID]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM run_records WHERE run_id = %s ORDER BY created_at, id", (run_id,)
            )
            return [row[0] for row in cur.fetchall()]

    def states(self, run_id: uuid.UUID) -> dict[str, str]:
        """``record_key -> state`` for a run."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT record_key, state FROM run_records WHERE run_id = %s", (run_id,))
            return {row[0]: row[1] for row in cur.fetchall()}

    def set_order(self, run_id: uuid.UUID, keys: list[str]) -> None:
        """Fix the claim order: the given record keys, earliest first.

        The claim orders by ``(created_at, id)`` and a run's rows share one ``created_at``, so the
        order would otherwise be decided by random uuids. A test that cares which record is claimed
        first calls this.
        """
        for index, key in enumerate(keys):
            self.conn.execute(
                "UPDATE run_records SET created_at = timestamptz '2000-01-01' "
                "+ make_interval(secs => %s) WHERE run_id = %s AND record_key = %s",
                (index, run_id, key),
            )

    def commit_key(self, key: str, run_id: uuid.UUID, *, via: uuid.UUID | None = None) -> None:
        """Put an idempotency key in ``committed_keys`` as if an earlier run had committed it."""
        record = via or self.record_ids(run_id)[0]
        self.conn.execute(
            "INSERT INTO committed_keys "
            "(tenant_id, destination_key, idempotency_key, workflow_id, run_record_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (self.tenant_id, DESTINATION_KEY, key, self.workflow_id, record),
        )

    def run_status(self, run_id: uuid.UUID) -> tuple[str, str | None]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT status, pause_reason FROM runs WHERE id = %s", (run_id,))
            return cur.fetchone()


@pytest.fixture
def seed(conn: psycopg.Connection) -> Callable[..., Seed]:
    """A factory: ``seed(recipe(...))`` builds a tenant/workflow/version/batch."""

    def factory(document: dict[str, Any] | None = None) -> Seed:
        return Seed(conn, document if document is not None else recipe())

    return factory


@pytest.fixture
def make_worker(database_url: str) -> Callable[..., Worker]:
    """Build a worker over the session database and a scripted set of fakes."""

    def factory(
        scripted: ScriptedRun,
        *,
        worker_id: str = "worker-1",
        heartbeat_interval: float = 3600.0,
        heartbeat_timeout_seconds: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
        fallback_timeout_ms: int = 10_000,
        reconciler: object | None = None,
        reconnect: Callable[[], bool] | None = None,
        gate_wait_seconds: float = 5.0,
        clock: object | None = None,
    ) -> Worker:
        return Worker(
            database_url,
            worker_id=worker_id,
            executor=scripted.executor,
            reconciler=reconciler or scripted.reconciler,
            clock=clock or scripted.clock,
            crash_hooks=scripted.crash_hooks,
            heartbeat_interval=heartbeat_interval,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            fallback_timeout_ms=fallback_timeout_ms,
            events=scripted.events,
            reconnect=reconnect,
            gate_wait_seconds=gate_wait_seconds,
        )

    return factory


__all__ = ["DEFAULT_STEPS", "Seed", "FakeDestination", "make_worker", "recipe"]
