"""PL-061: the API's share of the real manual-versus-automated time.

Home keeps its old ``minutes_saved`` so the screens that have not changed yet still work, and adds
the four numbers the brief actually compares: manual seconds, automated seconds, saved seconds,
and how many rows had to be estimated. The run detail adds the per-row form, null while the run is
open or has not finished a row.
"""

from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.db.seed import Ledger, Run

MANUAL_SECONDS = 120
FALLBACK_SECONDS = 180


def add_task(
    conn: psycopg.Connection,
    ledger: Ledger,
    *,
    name: str,
    manual_seconds: float | None,
) -> tuple[uuid.UUID, uuid.UUID]:
    meta: dict[str, object] = {"name": name, "allowed_origins": ["http://localhost:5173"]}
    if manual_seconds is not None:
        meta["manual_seconds_per_row"] = manual_seconds
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, %s, 'demo-crm', '{http://localhost:5173}') RETURNING id",
            (ledger.tenant_id, name),
        )
        workflow_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, 1, %s, 'compiled from recording') RETURNING id",
            (ledger.tenant_id, workflow_id, json.dumps({"meta": meta, "steps": []})),
        )
        return workflow_id, cur.fetchone()[0]


def add_run(
    conn: psycopg.Connection,
    ledger: Ledger,
    workflow_id: uuid.UUID,
    recipe_version_id: uuid.UUID,
    *,
    status: str = "completed",
    started_seconds_ago: float | None = 60,
    finished_seconds_ago: float | None = 0,
) -> Run:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO input_batches (tenant_id, workflow_id, kind) "
            "VALUES (%s, %s, 'csv') RETURNING id",
            (ledger.tenant_id, workflow_id),
        )
        batch_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO runs "
            "(tenant_id, workflow_id, recipe_version_id, input_batch_id, mode, status, "
            " destination_key, started_at, finished_at) "
            "VALUES (%s, %s, %s, %s, 'batch', %s, 'demo-crm', "
            " CASE WHEN %s::float8 IS NULL THEN NULL ELSE now() - make_interval("
            "secs => %s::float8) END, "
            " CASE WHEN %s::float8 IS NULL THEN NULL ELSE now() - make_interval("
            "secs => %s::float8) END) RETURNING id",
            (
                ledger.tenant_id,
                workflow_id,
                recipe_version_id,
                batch_id,
                status,
                started_seconds_ago,
                started_seconds_ago,
                finished_seconds_ago,
                finished_seconds_ago,
            ),
        )
        return Run(id=cur.fetchone()[0], batch_id=batch_id)


def add_done_rows(ledger: Ledger, run: Run, count: int) -> None:
    for _ in range(count):
        ledger.add_record(run, state="verified")


def home_body(connected: TestClient, ledger: Ledger) -> dict:
    response = connected.get(f"/api/v1/home?tenant_id={ledger.tenant_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_home_says_what_the_week_s_rows_were_worth_by_hand(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Three measured rows at 120 s against a 60-second run: 360, 60, 300, none estimated."""
    workflow, recipe = add_task(conn, ledger, name="Quotations", manual_seconds=MANUAL_SECONDS)
    run = add_run(conn, ledger, workflow, recipe)
    add_done_rows(ledger, run, 3)

    body = home_body(connected, ledger)

    assert body["manual_seconds"] == 3 * MANUAL_SECONDS
    assert body["automated_seconds"] == pytest.approx(60, abs=2)
    assert body["saved_seconds"] == pytest.approx(300, abs=2)
    assert body["estimated_rows"] == 0
    # The old field keeps working, in whole minutes, off the same number.
    assert body["minutes_saved"] == round(body["saved_seconds"] / 60)


def test_home_marks_the_rows_it_had_to_estimate(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    workflow, recipe = add_task(conn, ledger, name="Leads", manual_seconds=None)
    run = add_run(conn, ledger, workflow, recipe)
    add_done_rows(ledger, run, 2)

    body = home_body(connected, ledger)

    assert body["manual_seconds"] == 2 * FALLBACK_SECONDS
    assert body["estimated_rows"] == 2


def test_home_never_reports_a_negative_saving(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A run slower than the rows were worth saves nothing."""
    workflow, recipe = add_task(conn, ledger, name="Quotations", manual_seconds=60)
    run = add_run(conn, ledger, workflow, recipe, started_seconds_ago=600, finished_seconds_ago=0)
    add_done_rows(ledger, run, 1)

    body = home_body(connected, ledger)

    assert body["saved_seconds"] == 0


def test_the_run_detail_compares_one_row(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    workflow, recipe = add_task(conn, ledger, name="Quotations", manual_seconds=MANUAL_SECONDS)
    run = add_run(conn, ledger, workflow, recipe)
    add_done_rows(ledger, run, 3)

    body = connected.get(f"/api/v1/runs/{run.id}").json()

    assert body["manual_seconds_per_row"] == MANUAL_SECONDS
    assert body["automated_seconds_per_row"] == pytest.approx(20, abs=1)
    assert body["saved_seconds"] == pytest.approx(300, abs=2)


def test_the_run_detail_leaves_the_numbers_null_for_an_open_run(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    workflow, recipe = add_task(conn, ledger, name="Quotations", manual_seconds=MANUAL_SECONDS)
    run = add_run(
        conn,
        ledger,
        workflow,
        recipe,
        status="running",
        started_seconds_ago=60,
        finished_seconds_ago=None,
    )
    add_done_rows(ledger, run, 1)

    body = connected.get(f"/api/v1/runs/{run.id}").json()

    assert body["manual_seconds_per_row"] is None
    assert body["automated_seconds_per_row"] is None
    assert body["saved_seconds"] is None


def test_the_run_detail_leaves_the_numbers_null_when_no_row_finished(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    workflow, recipe = add_task(conn, ledger, name="Quotations", manual_seconds=MANUAL_SECONDS)
    run = add_run(conn, ledger, workflow, recipe)

    body = connected.get(f"/api/v1/runs/{run.id}").json()

    assert body["manual_seconds_per_row"] is None
    assert body["automated_seconds_per_row"] is None
    assert body["saved_seconds"] is None
