"""PL-067: the pacing columns on `runs`, and the tenant settings the site caps ride.

One run row is built from scratch (tenant -> workflow -> recipe version -> run), so the CHECK
and the defaults are exercised against a real row without any fixture beyond the schema.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest


def _one_run(conn: psycopg.Connection) -> uuid.UUID:
    tenant = conn.execute(
        "INSERT INTO tenants (name) VALUES ('Pacing test') RETURNING id"
    ).fetchone()[0]
    workflow = conn.execute(
        "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
        "VALUES (%s, 'Pacing', 'demo-crm', %s) RETURNING id",
        (tenant, ["http://localhost:5173"]),
    ).fetchone()[0]
    version = conn.execute(
        "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
        "VALUES (%s, %s, 1, %s, 'test') RETURNING id",
        (tenant, workflow, psycopg.types.json.Jsonb({"meta": {}, "steps": []})),
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO runs (tenant_id, workflow_id, recipe_version_id, mode, destination_key) "
        "VALUES (%s, %s, %s, 'batch', 'demo-crm') RETURNING id",
        (tenant, workflow, version),
    ).fetchone()[0]


def test_the_pacing_columns_exist_and_default_to_null(conn: psycopg.Connection) -> None:
    columns = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'runs' AND column_name IN "
        "('min_seconds_between_rows', 'min_seconds_between_steps')"
    ).fetchall()
    assert {name for name, _type in columns} == {
        "min_seconds_between_rows",
        "min_seconds_between_steps",
    }
    assert all(type == "smallint" for _name, type in columns)
    run_id = _one_run(conn)
    row = conn.execute(
        "SELECT min_seconds_between_rows, min_seconds_between_steps FROM runs WHERE id = %s",
        (run_id,),
    ).fetchone()
    assert row == (None, None)


@pytest.mark.parametrize("value", [-1, 3601])
def test_the_check_refuses_values_outside_0_3600(conn: psycopg.Connection, value: int) -> None:
    run_id = _one_run(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE runs SET min_seconds_between_rows = %s WHERE id = %s", (value, run_id))


def test_the_check_accepts_the_boundaries(conn: psycopg.Connection) -> None:
    run_id = _one_run(conn)
    conn.execute("UPDATE runs SET min_seconds_between_steps = 3600 WHERE id = %s", (run_id,))
    conn.execute("UPDATE runs SET min_seconds_between_steps = 0 WHERE id = %s", (run_id,))


def test_tenants_have_a_settings_column(conn: psycopg.Connection) -> None:
    tenant = conn.execute(
        "INSERT INTO tenants (name) VALUES ('Settings test') RETURNING id, settings"
    ).fetchone()
    assert tenant is not None and tenant[1] == {}
