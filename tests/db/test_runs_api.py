"""The runs and approvals API, against the real schema.

Every endpoint is exercised, including tenant scoping: a run's ids never reach another tenant.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from services.api.main import app

client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51234))

ORIGIN = "http://localhost:5173"
DESTINATION_KEY = "demo-crm"

VALID_RECIPE: dict[str, Any] = {
    "format_version": 1,
    "meta": {
        "name": "Create a lead",
        "when_to_use": "Each row must exist once in the CRM.",
        "input_schema": {
            "fields": {"email": {"type": "string", "required": True}},
            "rules": [{"when": {"missing": "email"}, "then": "park", "reason": "No email."}],
        },
        "record_key": "{{row.email | lower}}",
        "idempotency_key": "{{row.email | lower}}",
        "approval_policy": {"commit": "first_record"},
        "allowed_origins": [ORIGIN],
    },
    "steps": [
        {"id": "open", "action": "navigate", "url": f"{ORIGIN}/leads/new"},
        {
            "id": "fill-email",
            "action": "fill",
            "targets": [{"by": "label", "text": "Email"}],
            "value": {"kind": "input", "column": "email"},
        },
        {
            "id": "save",
            "action": "click",
            "commit": True,
            "assert_after": {"text_visible": "Lead created", "timeout_ms": 8000},
            "targets": [{"by": "role", "role": "button", "name": "Save"}],
        },
        {"id": "done", "action": "end_record", "outcome": "verified", "reason": "created"},
    ],
}


@pytest.fixture
def connected(monkeypatch: MonkeyPatch, database_url: str) -> TestClient:
    monkeypatch.setenv("DATABASE_URL", database_url)
    return client


class World:
    """One tenant with a workflow, a recipe version, a batch and some input records."""

    def __init__(self, conn: psycopg.Connection, tenant_name: str = "Synthetic tenant") -> None:
        self.conn = conn
        self.tenant_id = self._one(
            "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (tenant_name,)
        )
        self.workflow_id = self._one(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (self.tenant_id, f"Inquiries {tenant_name}", DESTINATION_KEY, [ORIGIN]),
        )
        self.recipe_version_id = self._one(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, 1, %s, 'test') RETURNING id",
            (self.tenant_id, self.workflow_id, json.dumps(VALID_RECIPE)),
        )
        self.batch_id = self._one(
            "INSERT INTO input_batches (tenant_id, workflow_id, kind) "
            "VALUES (%s, %s, 'csv') RETURNING id",
            (self.tenant_id, self.workflow_id),
        )
        self._row = 0

    def _one(self, sql: str, params: tuple = ()) -> Any:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]

    def add_input(
        self, email: str, *, status: str = "ready", issues: list[str] | None = None
    ) -> uuid.UUID:
        self._row += 1
        return self._one(
            "INSERT INTO input_records (tenant_id, batch_id, row_index, raw, clean, record_key, "
            "status, issues) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                self.tenant_id,
                self.batch_id,
                self._row,
                json.dumps({"email": email}),
                json.dumps({"email": email}),
                email if status == "ready" else None,
                status,
                json.dumps(issues or []),
            ),
        )

    def user(self, email: str = "reviewer@example.com") -> uuid.UUID:
        return self._one(
            "INSERT INTO users (tenant_id, email, name, role) "
            "VALUES (%s, %s, 'Reviewer', 'reviewer') RETURNING id",
            (self.tenant_id, f"{email}-{uuid.uuid4().hex[:6]}"),
        )

    def create_run(self, *, mode: str = "batch") -> uuid.UUID:
        return self._one(
            "INSERT INTO runs (tenant_id, workflow_id, recipe_version_id, input_batch_id, mode, "
            "status, destination_key, approval_policy) "
            "VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s) RETURNING id",
            (
                self.tenant_id,
                self.workflow_id,
                self.recipe_version_id,
                self.batch_id,
                mode,
                DESTINATION_KEY,
                json.dumps({"commit": "first_record"}),
            ),
        )

    def add_record(self, run_id: uuid.UUID, key: str, *, state: str = "pending") -> uuid.UUID:
        input_record_id = self.add_input(f"{key}@example.com")
        record_id = self._one(
            "INSERT INTO run_records (tenant_id, run_id, input_record_id, record_key, "
            "idempotency_key, state) VALUES (%s, %s, %s, %s, %s, 'pending') RETURNING id",
            (self.tenant_id, run_id, input_record_id, key, key),
        )
        if state != "pending":
            for step in _path_to(state):
                self.conn.execute(
                    "UPDATE run_records SET state = %s WHERE id = %s AND state <> %s",
                    (step, record_id, step),
                )
        return record_id

    def state_of(self, record_id: uuid.UUID) -> str:
        with self.conn.cursor() as cur:
            cur.execute("SELECT state FROM run_records WHERE id = %s", (record_id,))
            return cur.fetchone()[0]


def _path_to(state: str) -> list[str]:
    return {
        "parked": ["claimed", "parked"],
        "failed": ["claimed", "failed"],
        "submitting": ["claimed", "prechecked", "filling", "submitting"],
    }[state]


@pytest.fixture
def world(conn: psycopg.Connection) -> World:
    return World(conn)


# --- creating a run ---------------------------------------------------------


def test_create_a_run_returns_201(connected: TestClient, world: World) -> None:
    world.add_input("a@example.com")

    response = connected.post(
        "/api/v1/runs",
        json={
            "workflow_id": str(world.workflow_id),
            "recipe_version_id": str(world.recipe_version_id),
            "input_batch_id": str(world.batch_id),
            "mode": "batch",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "queued"
    detail = connected.get(f"/api/v1/runs/{body['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["counts"]["total"] == 1


def test_create_refuses_an_unsupported_action(connected: TestClient, world: World) -> None:
    broken = json.loads(json.dumps(VALID_RECIPE))
    broken["steps"].append(
        {
            "id": "x",
            "action": "transform",
            "as": "y",
            "op": "lower",
            "input": {"kind": "constant", "value": "a"},
        }
    )
    version = _store_recipe(world, broken)
    world.add_input("a@example.com")

    response = connected.post(
        "/api/v1/runs",
        json={
            "workflow_id": str(world.workflow_id),
            "recipe_version_id": str(version),
            "input_batch_id": str(world.batch_id),
            "mode": "batch",
        },
    )

    assert response.status_code == 422
    problems = response.json()["detail"]["problems"]
    assert any("unsupported step action" in problem for problem in problems)


def test_create_refuses_an_unknown_filter(connected: TestClient, world: World) -> None:
    broken = json.loads(json.dumps(VALID_RECIPE))
    broken["meta"]["record_key"] = "{{row.email | shout}}"
    version = _store_recipe(world, broken)
    world.add_input("a@example.com")

    response = connected.post(
        "/api/v1/runs",
        json={
            "workflow_id": str(world.workflow_id),
            "recipe_version_id": str(version),
            "input_batch_id": str(world.batch_id),
            "mode": "batch",
        },
    )

    assert response.status_code == 422
    assert any(
        "unknown template filter" in problem for problem in response.json()["detail"]["problems"]
    )


def test_create_refuses_a_missing_column(connected: TestClient, world: World) -> None:
    base = world
    base.add_input("a@example.com")
    broken = json.loads(json.dumps(VALID_RECIPE))
    broken["meta"]["record_key"] = "{{row.nope}}"
    version = _store_recipe(world, broken)

    response = connected.post(
        "/api/v1/runs",
        json={
            "workflow_id": str(world.workflow_id),
            "recipe_version_id": str(version),
            "input_batch_id": str(world.batch_id),
            "mode": "batch",
        },
    )

    assert response.status_code == 422
    assert any("no column" in problem for problem in response.json()["detail"]["problems"])


def test_create_refuses_two_rows_that_render_the_same_record_key(
    connected: TestClient, world: World, conn: psycopg.Connection
) -> None:
    base = world
    base.add_input("a@example.com")
    base.add_input("a@example.com")

    response = connected.post(
        "/api/v1/runs",
        json={
            "workflow_id": str(world.workflow_id),
            "recipe_version_id": str(world.recipe_version_id),
            "input_batch_id": str(world.batch_id),
            "mode": "batch",
        },
    )

    assert response.status_code == 422
    problems = response.json()["detail"]["problems"]
    assert any("duplicate record_key" in problem for problem in problems)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM runs WHERE workflow_id = %s", (world.workflow_id,))
        assert cur.fetchone()[0] == 0, "a refused creation writes no run"


def _store_recipe(world: World, document: dict[str, Any]) -> uuid.UUID:
    with world.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, (SELECT coalesce(max(version), 0) + 1 FROM recipe_versions "
            " WHERE workflow_id = %s), %s, 'test') RETURNING id",
            (world.tenant_id, world.workflow_id, world.workflow_id, json.dumps(document)),
        )
        return cur.fetchone()[0]


# --- reading a run ----------------------------------------------------------


def test_get_a_run_shows_status_counts_and_an_open_approval(
    connected: TestClient, world: World, conn: psycopg.Connection
) -> None:
    run_id = world.create_run()
    world.add_record(run_id, "a")
    record_id = world.add_record(run_id, "b")
    conn.execute("UPDATE run_records SET state = 'claimed' WHERE id = %s", (record_id,))
    conn.execute("UPDATE run_records SET state = 'parked' WHERE id = %s", (record_id,))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO approval_requests (tenant_id, run_id, run_record_id, step_id, kind, shown)"
            "VALUES (%s, %s, %s, 'save', 'commit', '{\"email\": \"a@example.com\"}')",
            (world.tenant_id, run_id, record_id),
        )

    response = connected.get(f"/api/v1/runs/{run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == str(world.tenant_id)
    assert body["status"] == "queued"
    assert body["counts"]["total"] == 2
    assert body["counts"]["parked"] == 1
    assert body["counts"]["in_progress"] == 1
    assert body["open_approval"]["step_id"] == "save"
    assert body["open_approval"]["shown"] == {"email": "a@example.com"}


def test_get_a_missing_run_is_404(connected: TestClient) -> None:
    assert connected.get(f"/api/v1/runs/{uuid.uuid4()}").status_code == 404


def test_list_records_returns_per_record_state(
    connected: TestClient, world: World, conn: psycopg.Connection
) -> None:
    run_id = world.create_run()
    record_id = world.add_record(run_id, "a", state="submitting")

    response = connected.get(f"/api/v1/runs/{run_id}/records")

    assert response.status_code == 200
    records = response.json()
    assert len(records) == 1
    assert records[0]["id"] == str(record_id)
    assert records[0]["state"] == "submitting"
    assert records[0]["commit_attempted"] is True
    assert records[0]["attempts"] == 1, "the walk to submitting claimed the record once"
    assert records[0]["max_attempts"] == 3


def test_list_transitions_returns_the_audit_feed(
    connected: TestClient, world: World, conn: psycopg.Connection
) -> None:
    run_id = world.create_run()
    world.add_record(run_id, "a", state="submitting")

    response = connected.get(f"/api/v1/runs/{run_id}/transitions")

    assert response.status_code == 200
    transitions = response.json()
    assert [row["to_state"] for row in transitions] == [
        "claimed",
        "prechecked",
        "filling",
        "submitting",
    ]
    after = transitions[-1]["id"]
    assert connected.get(f"/api/v1/runs/{run_id}/transitions?after={after}").json() == []


# --- lifecycle --------------------------------------------------------------


def test_pause_resume_and_cancel(connected: TestClient, world: World) -> None:
    run_id = world.create_run()

    paused = connected.post(f"/api/v1/runs/{run_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["pause_reason"] == "paused_by_operator"

    resumed = connected.post(f"/api/v1/runs/{run_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "queued"

    cancelled = connected.post(f"/api/v1/runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["cancel_requested_at"] is not None


def test_resume_on_a_running_run_is_409(connected: TestClient, world: World) -> None:
    run_id = world.create_run()
    with world.conn.cursor() as cur:
        cur.execute("UPDATE runs SET status = 'running' WHERE id = %s", (run_id,))

    assert connected.post(f"/api/v1/runs/{run_id}/resume").status_code == 409


def test_results_csv_has_a_ledger_row_per_record_and_the_excluded_rows(
    connected: TestClient, world: World, conn: psycopg.Connection
) -> None:
    run_id = world.create_run()
    world.add_record(run_id, "a", state="parked")
    world.add_record(run_id, "b")
    world.add_input("dup@example.com", status="excluded", issues=["duplicate in file"])

    response = connected.get(f"/api/v1/runs/{run_id}/results.csv")

    assert response.status_code == 200
    lines = response.text.strip().splitlines()
    assert lines[0].startswith("row_kind,record_key,state")
    assert len(lines) == 4, "a header, one row per ledger record, and the excluded row"
    assert any(line.startswith("excluded") and "duplicate in file" in line for line in lines)


# --- requeue ----------------------------------------------------------------


def test_requeue_a_plain_parked_record_goes_to_pending(connected: TestClient, world: World) -> None:
    run_id = world.create_run()
    record_id = world.add_record(run_id, "a", state="parked")

    response = connected.post(
        f"/api/v1/records/{record_id}/requeue", json={"reason": "fixed the row"}
    )

    assert response.status_code == 200
    assert response.json()["state"] == "pending"
    assert world.state_of(record_id) == "pending"


def test_requeue_a_commit_attempted_record_goes_to_reconciling(
    connected: TestClient, world: World
) -> None:
    run_id = world.create_run()
    record_id = world.add_record(run_id, "a", state="submitting")
    world.conn.execute("UPDATE run_records SET state = 'failed' WHERE id = %s", (record_id,))

    response = connected.post(f"/api/v1/records/{record_id}/requeue", json={})

    assert response.status_code == 200
    assert response.json()["state"] == "reconciling"
    assert world.state_of(record_id) == "reconciling"


# --- approvals --------------------------------------------------------------


def test_record_an_approval_and_resume_the_run(
    connected: TestClient, world: World, conn: psycopg.Connection
) -> None:
    run_id = world.create_run()
    record_id = world.add_record(run_id, "a")
    conn.execute(
        "UPDATE runs SET status = 'paused', pause_reason = 'awaiting_approval' WHERE id = %s",
        (run_id,),
    )
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO approval_requests (tenant_id, run_id, run_record_id, step_id, kind, shown)"
            "VALUES (%s, %s, %s, 'save', 'commit', '{}') RETURNING id",
            (world.tenant_id, run_id, record_id),
        )
        request_id = cur.fetchone()[0]

    response = connected.post(
        "/api/v1/approvals",
        json={
            "request_id": str(request_id),
            "decision": "approved",
            "decided_by": str(world.user()),
            "reason": "checked the values",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["decision"] == "approved"
    assert body["resumed"] is True
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] == "queued"
        cur.execute("SELECT request_id FROM approvals WHERE id = %s", (body["approval_id"],))
        assert cur.fetchone()[0] == request_id


def test_a_second_decision_on_the_same_request_is_409(
    connected: TestClient, world: World, conn: psycopg.Connection
) -> None:
    run_id = world.create_run()
    record_id = world.add_record(run_id, "a")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO approval_requests (tenant_id, run_id, run_record_id, step_id, kind) "
            "VALUES (%s, %s, %s, 'save', 'commit') RETURNING id",
            (world.tenant_id, run_id, record_id),
        )
        request_id = cur.fetchone()[0]
    payload = {
        "request_id": str(request_id),
        "decision": "approved",
        "decided_by": str(world.user()),
    }
    assert connected.post("/api/v1/approvals", json=payload).status_code == 201

    assert connected.post("/api/v1/approvals", json=payload).status_code == 409


def test_an_unknown_approval_request_is_404(connected: TestClient, world: World) -> None:
    response = connected.post(
        "/api/v1/approvals",
        json={
            "request_id": str(uuid.uuid4()),
            "decision": "approved",
            "decided_by": str(world.user()),
        },
    )
    assert response.status_code == 404


# --- tenant scoping ---------------------------------------------------------


def test_tenant_scoping_keeps_each_run_to_its_own_tenant(
    connected: TestClient, conn: psycopg.Connection
) -> None:
    first = World(conn, "Tenant one")
    second = World(conn, "Tenant two")
    run_one = first.create_run()
    run_two = second.create_run()
    first.add_record(run_one, "one")
    second.add_record(run_two, "two")

    assert connected.get(f"/api/v1/runs/{run_one}").json()["tenant_id"] == str(first.tenant_id)
    records_one = connected.get(f"/api/v1/runs/{run_one}/records").json()
    records_two = connected.get(f"/api/v1/runs/{run_two}/records").json()

    assert [row["record_key"] for row in records_one] == ["one"]
    assert [row["record_key"] for row in records_two] == ["two"]
    assert connected.get(f"/api/v1/runs/{run_one}/results.csv").text.count("one") >= 1
    assert "two" not in connected.get(f"/api/v1/runs/{run_one}/results.csv").text


def test_a_cross_tenant_pin_is_refused(connected: TestClient, conn: psycopg.Connection) -> None:
    first = World(conn, "Tenant one")
    second = World(conn, "Tenant two")
    first.add_input("a@example.com")

    response = connected.post(
        "/api/v1/runs",
        json={
            "workflow_id": str(first.workflow_id),
            "recipe_version_id": str(second.recipe_version_id),
            "input_batch_id": str(first.batch_id),
            "mode": "batch",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["problems"]


def test_a_missing_database_is_503(monkeypatch: MonkeyPatch) -> None:
    from services.api import db

    monkeypatch.setattr(db, "database_url", lambda: "")
    assert client.get(f"/api/v1/runs/{uuid.uuid4()}").status_code == 503
