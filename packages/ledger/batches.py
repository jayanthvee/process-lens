"""Input batches and their rows: one upload, its mapping, and the check's verdict.

Every statement that names ``input_batches`` or ``input_records`` lives here, so a service module
writes none of its own (``tests/runner/test_sql_separation.py``). The functions take a connection
and open no transaction of their own: the caller owns the boundary, because a batch and its rows
must land together or not at all.

A row is inserted ``ready`` with a ``record_key`` (``0001_init``'s check requires one), and the
check later moves the rows it could not accept to ``needs_review`` with a plain reason each. The
mapping a person confirmed is stored on the batch, never on the recipe.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

_INSERT_BATCH = """
INSERT INTO input_batches
  (tenant_id, workflow_id, file_id, kind, row_count, created_by, column_mapping)
VALUES
  (%(tenant)s, %(workflow)s, %(file)s, %(kind)s, %(row_count)s, %(created_by)s, %(mapping)s)
RETURNING id
"""

_INSERT_ROW = """
INSERT INTO input_records
  (tenant_id, batch_id, row_index, raw, clean, record_key, status, issues)
VALUES
  (%(tenant)s, %(batch)s, %(row_index)s, %(raw)s, %(clean)s, %(record_key)s, %(status)s, %(issues)s)
"""

_SELECT_BATCH = """
SELECT id, tenant_id, workflow_id, kind, row_count, file_id, column_mapping
FROM input_batches
WHERE id = %(id)s
"""

_SELECT_ROWS = """
SELECT id, row_index, raw, clean, record_key, status, issues
FROM input_records
WHERE batch_id = %(batch)s
ORDER BY row_index
"""

_UPDATE_MAPPING = """
UPDATE input_batches SET column_mapping = %(mapping)s WHERE id = %(id)s
"""

_UPDATE_VERDICT = """
UPDATE input_records SET status = %(status)s, issues = %(issues)s WHERE id = %(id)s
"""

_UPDATE_CLEAN = """
UPDATE input_records SET clean = %(clean)s WHERE id = %(id)s
"""


@dataclass(frozen=True)
class BatchRow:
    """One row on its way in: the person's row order, the values, and the key built from them."""

    row_index: int  # 1-based, the person's row order
    raw: dict[str, Any]  # exactly as uploaded
    clean: dict[str, Any]  # the values a run uses
    record_key: str
    status: str = "ready"  # 'ready' | 'needs_review'
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class BatchRowInfo:
    """An uploaded batch, without its rows."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    workflow_id: uuid.UUID
    kind: str
    row_count: int
    file_id: uuid.UUID | None
    column_mapping: dict[str, Any] | None


@dataclass(frozen=True)
class StoredRow:
    """One stored row, with the verdict the check left on it."""

    id: uuid.UUID
    row_index: int
    raw: dict[str, Any]
    clean: dict[str, Any]
    record_key: str | None
    status: str
    issues: tuple[str, ...]


def insert_batch(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    kind: str,
    rows: list[BatchRow],
    file_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    column_mapping: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Insert one ``input_batches`` row and one ``input_records`` row per row; return the batch id.

    ``row_count`` is the number of rows given. The caller opens the transaction: a batch whose rows
    did not all land is worse than no batch at all.
    """
    with conn.cursor() as cur:
        cur.execute(
            _INSERT_BATCH,
            {
                "tenant": tenant_id,
                "workflow": workflow_id,
                "file": file_id,
                "kind": kind,
                "row_count": len(rows),
                "created_by": created_by,
                "mapping": Jsonb(column_mapping) if column_mapping is not None else None,
            },
        )
        batch_id: uuid.UUID = cur.fetchone()[0]
        cur.executemany(
            _INSERT_ROW,
            [
                {
                    "tenant": tenant_id,
                    "batch": batch_id,
                    "row_index": row.row_index,
                    "raw": Jsonb(row.raw),
                    "clean": Jsonb(row.clean),
                    "record_key": row.record_key,
                    "status": row.status,
                    "issues": Jsonb(list(row.issues)),
                }
                for row in rows
            ],
        )
    return batch_id


def get_batch(conn: psycopg.Connection, batch_id: uuid.UUID) -> BatchRowInfo | None:
    """One batch's header rows, or ``None`` when there is no such batch."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_BATCH, {"id": batch_id})
        row = cur.fetchone()
    if row is None:
        return None
    mapping = row["column_mapping"]
    return BatchRowInfo(
        id=row["id"],
        tenant_id=row["tenant_id"],
        workflow_id=row["workflow_id"],
        kind=row["kind"],
        row_count=row["row_count"],
        file_id=row["file_id"],
        column_mapping=mapping if isinstance(mapping, dict) else None,
    )


def list_batch_rows(conn: psycopg.Connection, batch_id: uuid.UUID) -> list[StoredRow]:
    """Every row of a batch, in the person's row order."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_ROWS, {"batch": batch_id})
        rows = cur.fetchall()
    return [
        StoredRow(
            id=row["id"],
            row_index=row["row_index"],
            raw=row["raw"] if isinstance(row["raw"], dict) else {},
            clean=row["clean"] if isinstance(row["clean"], dict) else {},
            record_key=row["record_key"],
            status=row["status"],
            issues=tuple(str(issue) for issue in (row["issues"] or [])),
        )
        for row in rows
    ]


def set_batch_mapping(
    conn: psycopg.Connection, batch_id: uuid.UUID, mapping: dict[str, Any] | None
) -> None:
    """Store the mapping a person confirmed on the batch. The recipe is never touched."""
    with conn.cursor() as cur:
        cur.execute(
            _UPDATE_MAPPING,
            {"id": batch_id, "mapping": Jsonb(mapping) if mapping is not None else None},
        )


def set_row_verdict(
    conn: psycopg.Connection, row_id: uuid.UUID, *, status: str, issues: list[str] | tuple[str, ...]
) -> None:
    """Record what the check decided about one row: ``ready``, or ``needs_review`` with reasons."""
    with conn.cursor() as cur:
        cur.execute(
            _UPDATE_VERDICT,
            {"id": row_id, "status": status, "issues": Jsonb(list(issues))},
        )


def set_row_clean(conn: psycopg.Connection, row_id: uuid.UUID, clean: dict[str, Any]) -> None:
    """Replace the values a run uses for one row, leaving ``raw`` exactly as it was uploaded."""
    with conn.cursor() as cur:
        cur.execute(_UPDATE_CLEAN, {"id": row_id, "clean": Jsonb(clean)})


__all__ = [
    "BatchRow",
    "BatchRowInfo",
    "StoredRow",
    "get_batch",
    "insert_batch",
    "list_batch_rows",
    "set_batch_mapping",
    "set_row_clean",
    "set_row_verdict",
]
