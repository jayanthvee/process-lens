"""Synthetic ledger rows used by the database tests.

Nothing here is real data: one tenant, one workflow, one recipe version, and
records named ``record-0001`` upwards. Ids are whatever the database generates,
so tests compare like with like.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg

TENANT_NAME = "Synthetic tenant"
WORKFLOW_NAME = "Inquiries"
DESTINATION_KEY = "demo-crm"
ALLOWED_ORIGINS = "{http://localhost:5173}"
RECIPE = '{"steps": []}'
DEFAULT_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Run:
    """A run and the input batch its records belong to."""

    id: uuid.UUID
    batch_id: uuid.UUID


class Ledger:
    """Builds the rows a ledger test needs, with database-generated ids."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self._row_index = 0
        self.tenant_id: uuid.UUID = self._one(
            "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (TENANT_NAME,)
        )
        self.workflow_id: uuid.UUID = self._one(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (self.tenant_id, WORKFLOW_NAME, DESTINATION_KEY, ALLOWED_ORIGINS),
        )
        self.recipe_version_id: uuid.UUID = self._one(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, 1, %s, 'compiled from recording') RETURNING id",
            (self.tenant_id, self.workflow_id, RECIPE),
        )

    def _one(self, sql: str, params: tuple = ()) -> Any:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]

    def new_run(self, *, mode: str = "batch", status: str = "queued") -> Run:
        batch_id: uuid.UUID = self._one(
            "INSERT INTO input_batches (tenant_id, workflow_id, kind) "
            "VALUES (%s, %s, 'csv') RETURNING id",
            (self.tenant_id, self.workflow_id),
        )
        run_id: uuid.UUID = self._one(
            "INSERT INTO runs "
            "(tenant_id, workflow_id, recipe_version_id, input_batch_id, mode, status) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (self.tenant_id, self.workflow_id, self.recipe_version_id, batch_id, mode, status),
        )
        return Run(id=run_id, batch_id=batch_id)

    def add_record(
        self,
        run: Run,
        *,
        record_key: str | None = None,
        state: str = "pending",
        attempts: int = 0,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> uuid.UUID:
        """Insert one input record and its ledger row directly in ``state``."""
        self._row_index += 1
        key = record_key or f"record-{self._row_index:04d}"
        input_record_id = self._one(
            "INSERT INTO input_records (tenant_id, batch_id, row_index, raw, record_key, status) "
            "VALUES (%s, %s, %s, %s, %s, 'ready') RETURNING id",
            (
                self.tenant_id,
                run.batch_id,
                self._row_index,
                json.dumps({"email": f"{key}@example.com"}),
                key,
            ),
        )
        return self._one(
            "INSERT INTO run_records "
            "(tenant_id, run_id, input_record_id, record_key, state, attempts, max_attempts) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (self.tenant_id, run.id, input_record_id, key, state, attempts, max_attempts),
        )

    def seed_records(
        self, count: int, *, state: str = "pending", run: Run | None = None
    ) -> tuple[Run, list[uuid.UUID]]:
        """One run with ``count`` ledger rows, all in the same state."""
        target = run or self.new_run()
        return target, [self.add_record(target, state=state) for _ in range(count)]

    def count_committed_keys(self) -> int:
        return int(
            self._one("SELECT count(*) FROM committed_keys WHERE tenant_id = %s", (self.tenant_id,))
        )
