"""PL-120: migration 0013 adds ``workflows.removed_at`` without touching existing tasks.

The test builds a scratch database at the schema the branch started from: every migration before
0013, applied to an empty database. It inserts a task, then applies 0013 from the real migrations
directory, so the column is added to a schema that already has rows in it -- which is what a running
workspace will do.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import psycopg

from db.migrate import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
REMOVED_MIGRATION = "0013_task_removed.sql"


def _one_workflow(conn: psycopg.Connection, name: str) -> object:
    """Insert one tenant and one task, and return the task's id."""
    tenant_id = conn.execute(
        "INSERT INTO tenants (name) VALUES ('Removed migration test') RETURNING id"
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
        "VALUES (%s, %s, 'demo-crm', %s) RETURNING id",
        (tenant_id, name, ["http://localhost:5173"]),
    ).fetchone()[0]


def test_0013_applies_over_the_current_schema_and_existing_tasks_are_not_removed(
    scratch_database: Callable[[str], str], tmp_path: Path
) -> None:
    dsn = scratch_database("processlens_pl120")
    earlier = tmp_path / "migrations"
    earlier.mkdir()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name == REMOVED_MIGRATION:
            continue
        shutil.copy2(path, earlier / path.name)

    applied_before = apply_migrations(dsn, earlier, log=lambda _message: None)
    assert REMOVED_MIGRATION not in applied_before

    with psycopg.connect(dsn, autocommit=True) as conn:
        existing = _one_workflow(conn, "Taught before the upgrade")

    applied_now = apply_migrations(dsn, MIGRATIONS, log=lambda _message: None)
    assert applied_now == [REMOVED_MIGRATION]

    with psycopg.connect(dsn, autocommit=True) as conn:
        column = conn.execute(
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = 'workflows' AND column_name = 'removed_at'"
        ).fetchone()
        assert column == ("timestamp with time zone", "YES", None)
        assert (
            conn.execute("SELECT removed_at FROM workflows WHERE id = %s", (existing,)).fetchone()[
                0
            ]
            is None
        )

        new_task = _one_workflow(conn, "Taught after the upgrade")
        assert (
            conn.execute("SELECT removed_at FROM workflows WHERE id = %s", (new_task,)).fetchone()[
                0
            ]
            is None
        )
