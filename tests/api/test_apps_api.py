"""The Apps read: one app per site a task may use.

The style is ``tests/api/test_home_api.py``'s: the ``ledger`` fixture builds a fresh tenant, so
every assertion here is scoped to this test's own data in the shared session database (the route
takes the ``tenant_id`` filter for exactly that reason).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import psycopg
from fastapi.testclient import TestClient

from services.api.apps import app_host, app_name
from tests.db.seed import Ledger


def apps_body(connected: TestClient, ledger: Ledger) -> list[dict]:
    """`GET /api/v1/apps`, scoped to this test's tenant."""
    response = connected.get(f"/api/v1/apps?tenant_id={ledger.tenant_id}")
    assert response.status_code == 200, response.text
    return response.json()


def add_task(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    name: str,
    origins: list[str],
    archived: bool = False,
) -> tuple[uuid.UUID, uuid.UUID]:
    """A saved task: one workflow and its one recipe version, with a real document."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workflows "
            "(tenant_id, name, destination_key, allowed_origins, archived_at) "
            "VALUES (%s, %s, 'demo-crm', %s, CASE WHEN %s THEN now() ELSE NULL END) RETURNING id",
            (tenant_id, name, origins, archived),
        )
        workflow_id = cur.fetchone()[0]
        document = {"meta": {"name": name, "allowed_origins": origins}, "steps": []}
        cur.execute(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, 1, %s, 'compiled from recording') RETURNING id",
            (tenant_id, workflow_id, json.dumps(document)),
        )
        return workflow_id, cur.fetchone()[0]


def add_run(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    recipe_version_id: uuid.UUID,
    status: str,
    age_hours: int = 0,
) -> uuid.UUID:
    """One run of a task, created ``age_hours`` before now."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO runs "
            "(tenant_id, workflow_id, recipe_version_id, input_batch_id, mode, status, "
            " destination_key, created_at) "
            "VALUES (%s, %s, %s, NULL, 'batch', %s, 'demo-crm', "
            " now() - make_interval(hours => %s)) RETURNING id",
            (tenant_id, workflow_id, recipe_version_id, status, age_hours),
        )
        return cur.fetchone()[0]


def app_of(body: list[dict], origin: str) -> dict | None:
    """The one entry for an origin, or ``None``."""
    matches = [app for app in body if app["origin"] == origin]
    assert len(matches) <= 1, f"the same origin appears {len(matches)} times"
    return matches[0] if matches else None


def test_two_tasks_on_one_site_make_one_app_with_both_tasks(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Two tasks that may use the same site are one app, and both tasks are listed by name."""
    first, _ = add_task(
        conn, tenant_id=ledger.tenant_id, name="Add leads", origins=["https://twenty.example.com"]
    )
    second, _ = add_task(
        conn, tenant_id=ledger.tenant_id, name="Add people", origins=["https://twenty.example.com"]
    )

    body = apps_body(connected, ledger)

    twenty = app_of(body, "https://twenty.example.com")
    assert twenty is not None
    assert [task["name"] for task in twenty["tasks"]] == ["Add leads", "Add people"]
    assert [task["id"] for task in twenty["tasks"]] == [str(first), str(second)]
    assert twenty["host"] == "twenty.example.com"
    assert twenty["name"] == "Twenty"


def test_a_task_can_use_two_sites_and_appears_under_both(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A site is the unit: one task allowed on two sites is listed once on each."""
    task, _ = add_task(
        conn,
        tenant_id=ledger.tenant_id,
        name="Add leads",
        origins=["http://localhost:8069", "https://crm.example.com"],
    )

    body = apps_body(connected, ledger)

    odoo = app_of(body, "http://localhost:8069")
    crm = app_of(body, "https://crm.example.com")
    assert odoo is not None and crm is not None
    assert [entry["id"] for entry in odoo["tasks"]] == [str(task)]
    assert [entry["id"] for entry in crm["tasks"]] == [str(task)]


def test_an_archived_task_is_left_out(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """An archived task is not a task any more, so its site is not an app either."""
    add_task(
        conn,
        tenant_id=ledger.tenant_id,
        name="Retired task",
        origins=["https://retired.example.com"],
        archived=True,
    )
    kept, _ = add_task(
        conn,
        tenant_id=ledger.tenant_id,
        name="Add leads",
        origins=["https://kept.example.com"],
    )

    body = apps_body(connected, ledger)

    assert app_of(body, "https://retired.example.com") is None
    kept_app = app_of(body, "https://kept.example.com")
    assert kept_app is not None
    assert [entry["id"] for entry in kept_app["tasks"]] == [str(kept)]


def test_another_tenants_tasks_never_appear(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The read is scoped to one tenant: another tenant's site is not this page's app."""
    other_tenant = uuid.uuid4()
    conn.execute("INSERT INTO tenants (id, name) VALUES (%s, 'Other tenant')", (other_tenant,))
    add_task(
        conn,
        tenant_id=other_tenant,
        name="Their task",
        origins=["https://theirs.example.com"],
    )
    mine, _ = add_task(
        conn, tenant_id=ledger.tenant_id, name="My task", origins=["https://mine.example.com"]
    )

    body = apps_body(connected, ledger)

    assert app_of(body, "https://theirs.example.com") is None
    assert {entry["id"] for app in body for entry in app["tasks"]} == {
        str(mine),
        # This test's own fixture workflow, seeded by ``Ledger`` on its own site.
        str(ledger.workflow_id),
    }


def test_an_app_that_has_never_run_says_so(connected: TestClient, ledger: Ledger) -> None:
    """With no runs at all, an app's last run is null, not a made-up answer."""
    body = apps_body(connected, ledger)

    app = app_of(body, "http://localhost:5173")
    assert app is not None
    assert app["last_run_at"] is None
    assert app["last_run_status"] is None


def test_the_newest_run_of_any_task_on_the_site_wins(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The app's last run is the newest run of any task that may use it, and its status."""
    older_task, older_recipe = add_task(
        conn, tenant_id=ledger.tenant_id, name="Add leads", origins=["https://crm.example.com"]
    )
    newer_task, newer_recipe = add_task(
        conn, tenant_id=ledger.tenant_id, name="Add people", origins=["https://crm.example.com"]
    )
    add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=older_task,
        recipe_version_id=older_recipe,
        status="completed",
        age_hours=5,
    )
    newest = add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=newer_task,
        recipe_version_id=newer_recipe,
        status="running",
        age_hours=1,
    )

    body = apps_body(connected, ledger)

    app = app_of(body, "https://crm.example.com")
    assert app is not None
    assert app["last_run_status"] == "running"
    # The newest run is the one an hour old, not the one five hours old.
    newest_row = conn.execute("SELECT created_at FROM runs WHERE id = %s", (newest,)).fetchone()
    assert datetime.fromisoformat(app["last_run_at"]) == newest_row[0]


def test_the_response_keys_are_exactly_the_frozen_shape(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """No row contents, no extra fields: an app and its tasks carry exactly what was frozen."""
    task, _ = add_task(
        conn, tenant_id=ledger.tenant_id, name="Add leads", origins=["https://crm.example.com"]
    )

    body = apps_body(connected, ledger)

    app = app_of(body, "https://crm.example.com")
    assert app is not None
    assert set(app) == {"name", "host", "origin", "tasks", "last_run_at", "last_run_status"}
    assert set(app["tasks"][0]) == {"id", "name"}
    assert app["tasks"][0]["id"] == str(task)


def test_the_apps_are_sorted_by_name(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The page's order is the API's: by the app's name, so two reads never disagree."""
    add_task(conn, tenant_id=ledger.tenant_id, name="Add leads", origins=["https://zebra.test"])
    add_task(conn, tenant_id=ledger.tenant_id, name="Add people", origins=["http://localhost:8069"])

    body = apps_body(connected, ledger)

    names = [app["name"] for app in body]
    assert names == sorted(names)


def test_the_name_rules_are_the_ticket_s_own() -> None:
    """Odoo and Twenty are recognised by host or port; anything else is the host, no port."""
    assert app_name("http://localhost:8069") == "Odoo"
    assert app_name("https://odoo.example.com/web") == "Odoo"
    assert app_name("http://localhost:3000") == "Twenty"
    assert app_name("https://twenty.example.com") == "Twenty"
    assert app_name("https://crm.example.com/add") == "crm.example.com"
    assert app_name("http://localhost:5173") == "localhost"
    assert app_host("http://localhost:8069") == "localhost:8069"
    assert app_host("https://crm.example.com/add") == "crm.example.com"
