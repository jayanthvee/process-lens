"""The Home, needs-help and tasks reads.

The style is ``tests/api/test_runs_reads.py``'s: the ``ledger`` fixture builds a fresh tenant, so
every assertion here is scoped to this test's own data in the shared session database (each route
takes the ``tenant_id`` filter for exactly that reason).
"""

from __future__ import annotations

import json
import uuid

import psycopg
from fastapi.testclient import TestClient

from tests.db.seed import Ledger


def home_body(connected: TestClient, ledger: Ledger) -> dict:
    """`GET /api/v1/home`, scoped to this test's tenant."""
    response = connected.get(f"/api/v1/home?tenant_id={ledger.tenant_id}")
    assert response.status_code == 200, response.text
    return response.json()


def add_task(
    conn: psycopg.Connection, ledger: Ledger, *, name: str, origin: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """A saved task: one non-archived workflow and its one recipe version, with a real document."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, %s, 'demo-crm', %s) RETURNING id",
            (ledger.tenant_id, name, [origin]),
        )
        workflow_id = cur.fetchone()[0]
        document = {"meta": {"name": name, "allowed_origins": [origin]}, "steps": []}
        cur.execute(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, 1, %s, 'compiled from recording') RETURNING id",
            (ledger.tenant_id, workflow_id, json.dumps(document)),
        )
        return workflow_id, cur.fetchone()[0]


def test_home_answers_the_frozen_shape(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The counts, the minutes, the top needs-help rows, the live run and the tasks, all shaped."""
    ledger.seed_records(4, state="verified")
    ledger.seed_records(1, state="skipped")
    ledger.seed_records(2, state="parked")
    running, _ = ledger.seed_records(3, state="pending")
    conn.execute("UPDATE runs SET status = 'running' WHERE id = %s", (running.id,))

    body = home_body(connected, ledger)

    assert set(body) == {
        "window_days",
        "counts",
        "manual_seconds",
        "automated_seconds",
        "saved_seconds",
        "estimated_rows",
        "minutes_saved",
        "needs_help",
        "running",
        "tasks",
    }
    assert body["window_days"] == 7
    assert body["counts"] == {"done": 4, "already_there": 1, "needs_help": 2}
    # Four rows at the three-minute fallback, minus a run that has taken no measurable wall clock.
    # The task here has no measurement of its own, so every row is an estimate (PL-061).
    assert body["manual_seconds"] == 4 * 180
    assert body["estimated_rows"] == 4
    assert body["minutes_saved"] == 12

    assert set(body["counts"]) == {"done", "already_there", "needs_help"}
    record = body["needs_help"][0]
    assert set(record) == {
        "record_id",
        "run_id",
        "task_name",
        "row",
        "state",
        "reason",
        "commit_attempted",
    }
    assert record["state"] == "parked"
    assert record["task_name"] == "Inquiries"
    assert record["row"]
    assert record["commit_attempted"] is False
    assert record["reason"] is None

    live = body["running"][0]
    assert set(live) == {"run_id", "task_name", "done", "total", "already_there"}
    assert live["run_id"] == str(running.id)
    assert live["task_name"] == "Inquiries"
    assert (live["done"], live["total"], live["already_there"]) == (0, 3, 0)

    task = body["tasks"][0]
    assert set(task) == {"workflow_id", "recipe_version_id", "name", "app", "last_run"}
    assert task["workflow_id"] == str(ledger.workflow_id)
    assert task["name"] == "Inquiries"


def test_a_row_that_finished_before_the_window_is_not_counted(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The counts read the audit table's own timestamps, so an older finish is not this week's."""
    ledger.seed_records(1, state="verified")
    _, old_ids = ledger.seed_records(1, state="pending")
    # The audit trigger stamps ``now()`` on every row it writes, so a row that finished before the
    # window is placed by writing its audit row with an older ``at``. The read counts audit rows,
    # so this is the only honest way to test the window filter.
    conn.execute(
        "INSERT INTO run_record_transitions "
        "(tenant_id, run_record_id, from_state, to_state, reason, actor, at) "
        "VALUES (%s, %s, 'filling', 'verified', 'ran last month', 'worker', "
        "now() - interval '30 days')",
        (ledger.tenant_id, old_ids[0]),
    )

    body = home_body(connected, ledger)

    assert body["counts"]["done"] == 1


def test_the_minutes_saved_are_clamped_at_zero(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A run that took longer than the rows were worth saves nothing, never a negative number."""
    run = ledger.new_run()
    conn.execute(
        "UPDATE runs SET created_at = now() - interval '2 hours', "
        "started_at = now() - interval '2 hours', finished_at = now() WHERE id = %s",
        (run.id,),
    )

    body = home_body(connected, ledger)

    assert body["counts"]["done"] == 0
    assert body["minutes_saved"] == 0


def test_an_open_run_does_not_eat_the_time_saved(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A run paused for two days, or queued and never claimed, subtracts nothing until it ends."""
    ledger.seed_records(10, state="verified")
    paused = ledger.new_run()
    queued = ledger.new_run()
    conn.execute(
        "UPDATE runs SET status = 'paused', created_at = now() - interval '2 days', "
        "started_at = now() - interval '2 days' WHERE id = %s",
        (paused.id,),
    )
    conn.execute(
        "UPDATE runs SET created_at = now() - interval '2 days' WHERE id = %s", (queued.id,)
    )

    body = home_body(connected, ledger)

    assert body["counts"]["done"] == 10
    assert body["minutes_saved"] == 30


def test_a_finished_run_subtracts_its_start_to_finish_time(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Ten rows are thirty minutes by hand; a run that took ten minutes saved twenty."""
    ledger.seed_records(10, state="verified")
    run = ledger.new_run()
    conn.execute(
        "UPDATE runs SET status = 'completed', started_at = now() - interval '10 minutes', "
        "finished_at = now() WHERE id = %s",
        (run.id,),
    )

    body = home_body(connected, ledger)

    assert body["minutes_saved"] == 20


def test_a_queued_run_is_not_listed_as_running(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A run that has not started is not "running now": only `status = 'running'` is listed."""
    queued, _ = ledger.seed_records(1, state="pending")
    running, _ = ledger.seed_records(2, state="pending")
    conn.execute("UPDATE runs SET status = 'running' WHERE id = %s", (running.id,))

    body = home_body(connected, ledger)

    assert [live["run_id"] for live in body["running"]] == [str(running.id)]
    assert str(queued.id) not in [live["run_id"] for live in body["running"]]


def test_the_needs_help_panel_is_the_newest_three_in_order(
    connected: TestClient, ledger: Ledger
) -> None:
    """Newest first, capped at three, while the count is the whole set."""
    keys: list[str] = []
    for index in range(4):
        key = f"row-{index}"
        run = ledger.new_run()
        ledger.add_record(run, record_key=key, state="parked")
        keys.append(key)

    body = home_body(connected, ledger)

    assert [row["row"] for row in body["needs_help"]] == [keys[3], keys[2], keys[1]]
    assert body["counts"]["needs_help"] == 4


def test_the_count_is_the_length_of_the_needs_help_route(
    connected: TestClient, ledger: Ledger
) -> None:
    """The sidebar count and the page come from the same set, so they can never disagree."""
    ledger.seed_records(2, state="parked")
    ledger.seed_records(1, state="failed")

    body = home_body(connected, ledger)
    response = connected.get(f"/api/v1/needs-help?tenant_id={ledger.tenant_id}")

    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 3
    assert body["counts"]["needs_help"] == len(rows)
    assert [row["row"] for row in rows] == [row["row"] for row in body["needs_help"]]
    assert {row["state"] for row in rows} == {"parked", "failed"}


def test_the_tasks_are_capped_with_the_app_from_the_first_allowed_origin(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Five tasks on Home, each named as its recipe is and pointing at the host it may use."""
    for index in range(6):
        add_task(conn, ledger, name=f"Add inquiries {index}", origin="https://crm.example.com/add")

    body = home_body(connected, ledger)

    assert len(body["tasks"]) == 5
    assert {task["app"] for task in body["tasks"]} == {"crm.example.com"}
    assert all(task["name"].startswith("Add inquiries ") for task in body["tasks"])


def test_a_task_that_has_never_run_has_no_last_run(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A task with no runs yet says so, and a task that has run carries its newest run."""
    workflow_id, recipe_version_id = add_task(
        conn, ledger, name="Add leads", origin="https://twenty.example.com"
    )
    never, never_recipe = add_task(
        conn, ledger, name="Update prices", origin="https://inventory.example.com"
    )
    conn.execute(
        "INSERT INTO runs "
        "(tenant_id, workflow_id, recipe_version_id, input_batch_id, mode, status, "
        "destination_key) "
        "VALUES (%s, %s, %s, NULL, 'batch', 'completed', 'demo-crm')",
        (ledger.tenant_id, workflow_id, recipe_version_id),
    )

    response = connected.get(f"/api/v1/tasks?tenant_id={ledger.tenant_id}")

    assert response.status_code == 200, response.text
    tasks = {task["workflow_id"]: task for task in response.json()}
    assert tasks[str(never)]["name"] == "Update prices"
    assert tasks[str(never)]["recipe_version_id"] == str(never_recipe)
    assert tasks[str(never)]["last_run"] is None
    ran = tasks[str(workflow_id)]["last_run"]
    assert ran is not None
    assert ran["status"] == "completed"
    assert ran["started_at"] is None
    assert ran["counts"] == {}


def test_the_needs_help_route_is_newest_first_with_the_task_name(
    connected: TestClient, ledger: Ledger
) -> None:
    """Each row carries the row it came from, the task it belongs to and its plain reason."""
    run = ledger.new_run()
    first = ledger.add_record(run, record_key="ACME-1", state="parked")
    second = ledger.add_record(run, record_key="ACME-2", state="failed")

    rows = connected.get(f"/api/v1/needs-help?tenant_id={ledger.tenant_id}").json()

    assert {row["record_id"] for row in rows} == {str(first), str(second)}
    assert all(row["task_name"] == "Inquiries" for row in rows)
    assert all(row["run_id"] == str(run.id) for row in rows)
    assert [row["row"] for row in rows] == ["ACME-2", "ACME-1"]
