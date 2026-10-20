"""PL-061: the real manual-versus-automated time, measured from the recipes.

The hard-coded three minutes per row is replaced by two reads: one for a single run (the done
rows, the run's wall clock, and the manual seconds its pinned recipe remembers) and one for the
Home window (done rows times their own recipe's manual seconds, with 180 seconds for a recipe
that has no measurement). Both live in ``packages.ledger.reads`` so the API still writes no SQL.
"""

from __future__ import annotations

import json
import uuid

import psycopg

from packages.ledger.reads import run_time, week_manual_seconds
from tests.db.seed import Ledger, Run

MANUAL_SECONDS = 120
FALLBACK_SECONDS = 180


def add_task(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    name: str,
    manual_seconds: float | None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """A workflow and its one recipe version, with or without the manual measurement."""
    meta: dict[str, object] = {"name": name}
    if manual_seconds is not None:
        meta["manual_seconds_per_row"] = manual_seconds
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, %s, 'demo-crm', '{http://localhost:5173}') RETURNING id",
            (tenant_id, name),
        )
        workflow_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, 1, %s, 'compiled from recording') RETURNING id",
            (tenant_id, workflow_id, json.dumps({"meta": meta, "steps": []})),
        )
        return workflow_id, cur.fetchone()[0]


def add_run(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    recipe_version_id: uuid.UUID,
    started_seconds_ago: float | None,
    finished_seconds_ago: float | None,
) -> Run:
    """One run pinned to a recipe version, with the wall clock a finished run would have."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO input_batches (tenant_id, workflow_id, kind) "
            "VALUES (%s, %s, 'csv') RETURNING id",
            (tenant_id, workflow_id),
        )
        batch_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO runs "
            "(tenant_id, workflow_id, recipe_version_id, input_batch_id, mode, status, "
            " destination_key, started_at, finished_at) "
            "VALUES (%s, %s, %s, %s, 'batch', 'completed', 'demo-crm', "
            " CASE WHEN %s::float8 IS NULL THEN NULL ELSE now() - make_interval("
            "secs => %s::float8) END, "
            " CASE WHEN %s::float8 IS NULL THEN NULL ELSE now() - make_interval("
            "secs => %s::float8) END) RETURNING id",
            (
                tenant_id,
                workflow_id,
                recipe_version_id,
                batch_id,
                started_seconds_ago,
                started_seconds_ago,
                finished_seconds_ago,
                finished_seconds_ago,
            ),
        )
        return Run(id=cur.fetchone()[0], batch_id=batch_id)


def add_rows(ledger: Ledger, run: Run, count: int, *, state: str = "verified") -> list[uuid.UUID]:
    return [ledger.add_record(run, state=state) for _ in range(count)]


def test_a_run_with_three_done_rows_reports_its_own_arithmetic(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    """3 rows at 120 s by hand, a 60-second run: 360 manual, 60 automated, 300 saved."""
    workflow, recipe = add_task(
        conn, tenant_id=ledger.tenant_id, name="Quotations", manual_seconds=MANUAL_SECONDS
    )
    run = add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=workflow,
        recipe_version_id=recipe,
        started_seconds_ago=60,
        finished_seconds_ago=0,
    )
    add_rows(ledger, run, 3)

    time = run_time(conn, run.id)

    assert time is not None
    assert time.done == 3
    assert time.manual_seconds_per_row == MANUAL_SECONDS
    assert time.automated_seconds is not None
    assert abs(time.automated_seconds - 60) < 1
    manual = time.done * time.manual_seconds_per_row
    assert round(manual - time.automated_seconds) == 300


def test_the_home_window_sums_each_task_s_own_measurement(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    workflow, recipe = add_task(
        conn, tenant_id=ledger.tenant_id, name="Quotations", manual_seconds=MANUAL_SECONDS
    )
    run = add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=workflow,
        recipe_version_id=recipe,
        started_seconds_ago=60,
        finished_seconds_ago=0,
    )
    add_rows(ledger, run, 3)

    week = week_manual_seconds(conn, days=7, tenant_id=ledger.tenant_id)

    assert week.manual_seconds == 3 * MANUAL_SECONDS
    assert week.estimated_rows == 0


def test_a_task_without_the_measurement_uses_the_fallback_and_is_counted(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A task that remembers nothing still has a number, and the screen must be able to say so."""
    measured_workflow, measured_recipe = add_task(
        conn, tenant_id=ledger.tenant_id, name="Quotations", manual_seconds=MANUAL_SECONDS
    )
    plain_workflow, plain_recipe = add_task(
        conn, tenant_id=ledger.tenant_id, name="Leads", manual_seconds=None
    )
    measured_run = add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=measured_workflow,
        recipe_version_id=measured_recipe,
        started_seconds_ago=60,
        finished_seconds_ago=0,
    )
    plain_run = add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=plain_workflow,
        recipe_version_id=plain_recipe,
        started_seconds_ago=60,
        finished_seconds_ago=0,
    )
    add_rows(ledger, measured_run, 1)
    add_rows(ledger, plain_run, 2)

    week = week_manual_seconds(conn, days=7, tenant_id=ledger.tenant_id)

    assert week.manual_seconds == MANUAL_SECONDS + 2 * FALLBACK_SECONDS
    assert week.estimated_rows == 2


def test_another_tenant_s_runs_are_not_counted(conn: psycopg.Connection, ledger: Ledger) -> None:
    workflow, recipe = add_task(
        conn, tenant_id=ledger.tenant_id, name="Quotations", manual_seconds=MANUAL_SECONDS
    )
    my_run = add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=workflow,
        recipe_version_id=recipe,
        started_seconds_ago=60,
        finished_seconds_ago=0,
    )
    add_rows(ledger, my_run, 1)

    with conn.cursor() as cur:
        cur.execute("INSERT INTO tenants (name) VALUES ('Another PL-061 tenant') RETURNING id")
        other_tenant = cur.fetchone()[0]
    other_workflow, other_recipe = add_task(
        conn, tenant_id=other_tenant, name="Theirs", manual_seconds=None
    )
    their_run = add_run(
        conn,
        tenant_id=other_tenant,
        workflow_id=other_workflow,
        recipe_version_id=other_recipe,
        started_seconds_ago=60,
        finished_seconds_ago=0,
    )
    with conn.cursor() as cur:
        for index in range(5):
            cur.execute(
                "INSERT INTO input_records "
                "(tenant_id, batch_id, row_index, raw, record_key, status) "
                "VALUES (%s, %s, %s, '{}'::jsonb, %s, 'ready') RETURNING id",
                (other_tenant, their_run.batch_id, index, f"theirs-{index}"),
            )
            input_record_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO run_records "
                "(tenant_id, run_id, input_record_id, record_key, state, attempts, "
                " max_attempts, idempotency_key) "
                "VALUES (%s, %s, %s, %s, 'pending', 0, 3, %s) RETURNING id",
                (
                    other_tenant,
                    their_run.id,
                    input_record_id,
                    f"theirs-{index}",
                    f"theirs-{index}",
                ),
            )
            record_id = cur.fetchone()[0]
            # The insert guard requires a new ledger row to be ``pending``; walk it to verified
            # along the one legal path, exactly as ``seed.py`` does.
            for state in ("claimed", "prechecked", "filling", "verified"):
                cur.execute(
                    "UPDATE run_records SET state = %s WHERE id = %s AND state <> %s",
                    (state, record_id, state),
                )

    week = week_manual_seconds(conn, days=7, tenant_id=ledger.tenant_id)

    # Only this test's tenant: one measured row, nothing estimated.
    assert week.manual_seconds == MANUAL_SECONDS
    assert week.estimated_rows == 0


def test_an_open_run_has_no_wall_clock(conn: psycopg.Connection, ledger: Ledger) -> None:
    workflow, recipe = add_task(
        conn, tenant_id=ledger.tenant_id, name="Quotations", manual_seconds=MANUAL_SECONDS
    )
    run = add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=workflow,
        recipe_version_id=recipe,
        started_seconds_ago=None,
        finished_seconds_ago=None,
    )
    add_rows(ledger, run, 1)

    time = run_time(conn, run.id)

    assert time is not None
    assert time.done == 1
    assert time.automated_seconds is None


def test_a_run_with_no_rows_still_reads(conn: psycopg.Connection, ledger: Ledger) -> None:
    workflow, recipe = add_task(
        conn, tenant_id=ledger.tenant_id, name="Quotations", manual_seconds=MANUAL_SECONDS
    )
    run = add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=workflow,
        recipe_version_id=recipe,
        started_seconds_ago=60,
        finished_seconds_ago=0,
    )

    time = run_time(conn, run.id)

    assert time is not None
    assert time.done == 0


def test_an_unknown_run_has_no_time(conn: psycopg.Connection) -> None:
    assert run_time(conn, uuid.uuid4()) is None
