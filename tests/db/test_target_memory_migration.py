"""PL-122: target_memory is keyed by tenant, workflow, step and origin, and a hit is counted.

The table remembers only a ladder that worked. It never changes a recipe, so its unique key is the
whole contract: one memory per step per site, updated in place.
"""

from __future__ import annotations

import uuid

import psycopg
import psycopg.types.json


def _tenant_workflow(conn: psycopg.Connection) -> tuple[uuid.UUID, uuid.UUID]:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO tenants (name) VALUES ('Memory tenant') RETURNING id")
        tenant = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, 'Price update', 'odoo', ARRAY['http://localhost:8069']) RETURNING id",
            (tenant,),
        )
        return tenant, cur.fetchone()[0]


def test_one_memory_per_step_and_origin_is_updated_in_place(
    conn: psycopg.Connection,
) -> None:
    tenant, workflow = _tenant_workflow(conn)
    ladder = psycopg.types.json.Jsonb(
        [{"by": "role", "role": "button", "name": "Oak Standing Desk"}]
    )
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO target_memory "
            "(tenant_id, workflow_id, step_id, origin, ladder, hit_count) "
            "VALUES (%s, %s, 'click-2', 'http://localhost:8069', %s, 1) RETURNING id",
            (tenant, workflow, ladder),
        )
        memory_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO target_memory "
            "(tenant_id, workflow_id, step_id, origin, ladder, hit_count) "
            "VALUES (%s, %s, 'click-2', 'http://localhost:8069', %s, 1) "
            "ON CONFLICT (tenant_id, workflow_id, step_id, origin) DO UPDATE "
            "SET hit_count = target_memory.hit_count + 1, last_used_at = now() "
            "RETURNING id, hit_count",
            (tenant, workflow, ladder),
        )
        returned_id, hit_count = cur.fetchone()

    assert returned_id == memory_id
    assert hit_count == 2

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM target_memory WHERE tenant_id = %s AND step_id = 'click-2'",
            (tenant,),
        )
        assert cur.fetchone()[0] == 1


def test_a_recipe_is_not_touched_by_a_memory_write(conn: psycopg.Connection) -> None:
    tenant, workflow = _tenant_workflow(conn)
    recipe = {"format_version": 1, "meta": {"name": "x"}, "steps": []}
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, 1, %s, 'test') RETURNING recipe",
            (tenant, workflow, psycopg.types.json.Jsonb(recipe)),
        )
        before = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO target_memory (tenant_id, workflow_id, step_id, origin, ladder) "
            "VALUES (%s, %s, 'click-2', 'http://localhost:8069', %s)",
            (
                tenant,
                workflow,
                psycopg.types.json.Jsonb(
                    [{"by": "role", "role": "button", "name": "Oak Standing Desk"}]
                ),
            ),
        )
        cur.execute(
            "SELECT recipe FROM recipe_versions WHERE tenant_id = %s AND workflow_id = %s",
            (tenant, workflow),
        )
        assert cur.fetchone()[0] == before
