"""PL-080: a run's pace is chosen on screen and stored on the run.

The API accepts two optional integers, each 0-3600 seconds, and stores them on the run row that
PL-067's runner reads (``min_seconds_between_rows`` / ``min_seconds_between_steps``). Missing means
unset, which the runner reads as 0 -- today's behaviour -- and the run detail answers both numbers
so the run page can say which pace is set.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

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
        "approval_policy": {"commit": "none"},
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


class World:
    """One tenant with a runnable task, a batch of two ready rows, and a way to start a run."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self.tenant_id: uuid.UUID = self._one(
            "INSERT INTO tenants (name) VALUES ('Synthetic tenant') RETURNING id"
        )
        self.workflow_id: uuid.UUID = self._one(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, 'Inquiries', %s, %s) RETURNING id",
            (self.tenant_id, DESTINATION_KEY, [ORIGIN]),
        )
        self.recipe_version_id: uuid.UUID = self._one(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, 1, %s, 'test') RETURNING id",
            (self.tenant_id, self.workflow_id, json.dumps(VALID_RECIPE)),
        )
        self.batch_id: uuid.UUID = self._one(
            "INSERT INTO input_batches (tenant_id, workflow_id, kind) "
            "VALUES (%s, %s, 'csv') RETURNING id",
            (self.tenant_id, self.workflow_id),
        )
        for index, email in enumerate(("a@example.com", "b@example.com")):
            self._one(
                "INSERT INTO input_records (tenant_id, batch_id, row_index, raw, clean, "
                " record_key, status) VALUES (%s, %s, %s, %s, %s, %s, 'ready') RETURNING id",
                (
                    self.tenant_id,
                    self.batch_id,
                    index,
                    json.dumps({"email": email}),
                    json.dumps({"email": email}),
                    email,
                ),
            )

    def _one(self, sql: str, params: tuple = ()) -> Any:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]

    def body(self, **pace: int) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "workflow_id": str(self.workflow_id),
            "recipe_version_id": str(self.recipe_version_id),
            "input_batch_id": str(self.batch_id),
            "mode": "batch",
        }
        payload.update(pace)
        return payload

    def stored_pace(self, run_id: str) -> tuple[int | None, int | None]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT min_seconds_between_rows, min_seconds_between_steps "
                "FROM runs WHERE id = %s",
                (uuid.UUID(run_id),),
            )
            row = cur.fetchone()
            assert row is not None
            return (row[0], row[1])


@pytest.fixture
def connected(monkeypatch: MonkeyPatch, database_url: str, client: TestClient) -> TestClient:
    monkeypatch.setenv("DATABASE_URL", database_url)
    return client


@pytest.fixture
def world(conn: psycopg.Connection) -> World:
    return World(conn)


def test_the_chosen_pace_is_stored_on_the_run(connected: TestClient, world: World) -> None:
    """Steady: 10 seconds between rows, 2 between steps, stored on the run row."""
    response = connected.post(
        "/api/v1/runs",
        json=world.body(min_seconds_between_rows=10, min_seconds_between_steps=2),
    )

    assert response.status_code == 201, response.text
    run_id = response.json()["run_id"]
    assert world.stored_pace(run_id) == (10, 2)


def test_the_run_page_is_told_the_pace_that_is_set(connected: TestClient, world: World) -> None:
    created = connected.post(
        "/api/v1/runs",
        json=world.body(min_seconds_between_rows=30, min_seconds_between_steps=5),
    )
    run_id = created.json()["run_id"]

    detail = connected.get(f"/api/v1/runs/{run_id}")

    assert detail.status_code == 200, detail.text
    assert detail.json()["min_seconds_between_rows"] == 30
    assert detail.json()["min_seconds_between_steps"] == 5


def test_a_run_with_no_pace_is_unset_and_reads_as_zero(connected: TestClient, world: World) -> None:
    """As fast as the site allows is today's behaviour: nothing stored, 0 on screen."""
    created = connected.post("/api/v1/runs", json=world.body())

    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]
    assert world.stored_pace(run_id) == (None, None)

    detail = connected.get(f"/api/v1/runs/{run_id}").json()
    assert detail["min_seconds_between_rows"] == 0
    assert detail["min_seconds_between_steps"] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_seconds_between_rows", -1),
        ("min_seconds_between_steps", -1),
        ("min_seconds_between_rows", 3601),
        ("min_seconds_between_steps", 3601),
    ],
)
def test_a_pace_outside_zero_to_3600_is_refused(
    connected: TestClient, world: World, field: str, value: int
) -> None:
    response = connected.post("/api/v1/runs", json=world.body(**{field: value}))

    assert response.status_code == 422, response.text
    with world.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM runs WHERE tenant_id = %s", (world.tenant_id,))
        assert cur.fetchone()[0] == 0, "nothing was written for a refused pace"


def test_the_ends_of_the_range_are_allowed(connected: TestClient, world: World) -> None:
    zero = connected.post(
        "/api/v1/runs",
        json=world.body(min_seconds_between_rows=0, min_seconds_between_steps=0),
    )
    assert zero.status_code == 201, zero.text
    assert world.stored_pace(zero.json()["run_id"]) == (0, 0)

    hour = connected.post(
        "/api/v1/runs",
        json=world.body(min_seconds_between_rows=3600, min_seconds_between_steps=3600),
    )
    assert hour.status_code == 201, hour.text
    assert world.stored_pace(hour.json()["run_id"]) == (3600, 3600)
