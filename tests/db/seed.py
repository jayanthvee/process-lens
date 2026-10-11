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

# The legal path from ``pending`` to each state. A ledger row is now inserted only as ``pending``
# (the insert guard), so a test that needs a row in another state walks it there along these paths.
# Every step here is legal under the guard; ``verified`` uses the read-only path so a test row is
# not needlessly marked commit-attempted.
STATE_PATHS: dict[str, list[str]] = {
    "pending": [],
    "claimed": ["claimed"],
    "prechecked": ["claimed", "prechecked"],
    "filling": ["claimed", "prechecked", "filling"],
    "submitting": ["claimed", "prechecked", "filling", "submitting"],
    "submitted_unverified": [
        "claimed",
        "prechecked",
        "filling",
        "submitting",
        "submitted_unverified",
    ],
    "verified": ["claimed", "prechecked", "filling", "verified"],
    "skipped": ["skipped"],
    "parked": ["claimed", "parked"],
    "failed": ["failed"],
    "reconciling": ["claimed", "prechecked", "filling", "submitting", "reconciling"],
}


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
            "(tenant_id, workflow_id, recipe_version_id, input_batch_id, mode, status, "
            " destination_key) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                self.tenant_id,
                self.workflow_id,
                self.recipe_version_id,
                batch_id,
                mode,
                status,
                DESTINATION_KEY,
            ),
        )
        return Run(id=run_id, batch_id=batch_id)

    def _advance(self, record_id: uuid.UUID, states: list[str]) -> None:
        """Move a record through a list of legal states, one conditional update each."""
        for state in states:
            self.conn.execute(
                "UPDATE run_records SET state = %s WHERE id = %s AND state <> %s",
                (state, record_id, state),
            )

    def add_record(
        self,
        run: Run,
        *,
        record_key: str | None = None,
        state: str = "pending",
        attempts: int = 0,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        idempotency_key: str | None = None,
    ) -> uuid.UUID:
        """Insert one input record and its ledger row, then walk it to ``state``.

        The row is born ``pending`` (the insert guard requires it) and reaches ``state`` and
        ``attempts`` through legal transitions, so a test cannot forge an outcome the database would
        reject in production.
        """
        self._row_index += 1
        key = record_key or f"record-{self._row_index:04d}"
        idem = idempotency_key if idempotency_key is not None else key
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
        record_id: uuid.UUID = self._one(
            "INSERT INTO run_records "
            "(tenant_id, run_id, input_record_id, record_key, state, attempts, max_attempts, "
            " idempotency_key) "
            "VALUES (%s, %s, %s, %s, 'pending', 0, %s, %s) RETURNING id",
            (self.tenant_id, run.id, input_record_id, key, max_attempts, idem),
        )
        # claims raise attempts by one each and are released back to pending
        self._advance(record_id, ["claimed", "pending"] * attempts)
        self._advance(record_id, STATE_PATHS[state])
        return record_id

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
