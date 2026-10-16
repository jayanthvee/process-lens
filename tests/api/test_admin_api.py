"""PL-026: the admin surface, the run gate, and the workspace state the page reads.

Everything here goes through the real HTTP front door against the real schema, because the two
things this ticket can get wrong are exactly the things a unit test would miss: which sentence a
refused request carries, and whether an admin action and its audit row really land together.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from packages.ledger import freeze_runs, get_extension_report
from services.api.admin import ADMIN_TOKEN_ENV_VAR, EXTENSION_FLOOR_TEMPLATE, RUNS_FROZEN_DETAIL
from services.api.operator import OPERATOR_ENV_VAR

TOKEN = "admin-token-for-tests"
TOKEN_HEADERS = {"X-ProcessLens-Admin-Token": TOKEN}
ORIGIN = "http://localhost:5173"
DESTINATION_KEY = "demo-crm"

VALID_RECIPE: dict[str, Any] = {
    "format_version": 1,
    "meta": {
        "name": "Create a lead",
        "when_to_use": "Each row must exist once in the CRM.",
        "input_schema": {
            "fields": {"email": {"type": "string", "required": True}},
            "rules": [
                {
                    "when": {"missing": "email"},
                    "then": "park",
                    "reason": "No email address for this row.",
                }
            ],
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
    """One tenant with a runnable workflow, a batch, a person, and a way to start a run."""

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
        self._row = 0

    def _one(self, sql: str, params: tuple = ()) -> Any:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]

    def add_input(self, email: str) -> uuid.UUID:
        self._row += 1
        return self._one(
            "INSERT INTO input_records (tenant_id, batch_id, row_index, raw, clean, record_key, "
            "status) VALUES (%s, %s, %s, %s, %s, %s, 'ready') RETURNING id",
            (
                self.tenant_id,
                self.batch_id,
                self._row,
                json.dumps({"email": email}),
                json.dumps({"email": email}),
                email,
            ),
        )

    def person(self, *, role: str = "owner", deactivated: bool = False) -> uuid.UUID:
        return self._one(
            "INSERT INTO users (tenant_id, email, name, role, deactivated_at) "
            "VALUES (%s, %s, 'A person', %s, CASE WHEN %s THEN now() ELSE NULL END) RETURNING id",
            (self.tenant_id, f"{uuid.uuid4().hex[:8]}@example.com", role, deactivated),
        )

    def create_run(self) -> str:
        response = _client().post(
            "/api/v1/runs",
            json={
                "workflow_id": str(self.workflow_id),
                "recipe_version_id": str(self.recipe_version_id),
                "input_batch_id": str(self.batch_id),
                "mode": "batch",
            },
        )
        assert response.status_code == 201, response.text
        return str(response.json()["run_id"])

    def audit_rows(self) -> list[tuple[str, str, str | None]]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT action, module, target FROM admin_audit WHERE tenant_id = %s ORDER BY id",
                (self.tenant_id,),
            )
            return [(row[0], row[1], row[2]) for row in cur.fetchall()]


def _client() -> TestClient:
    """The local test client: the same loopback peer every other api test uses."""
    from services.api.main import app

    return TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51234))


@pytest.fixture
def connected(monkeypatch: MonkeyPatch, database_url: str) -> TestClient:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv(ADMIN_TOKEN_ENV_VAR, TOKEN)
    return _client()


@pytest.fixture
def world(conn: psycopg.Connection) -> World:
    return World(conn)


# --- the shared token -------------------------------------------------------


def test_an_unset_admin_token_closes_the_admin_api(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    """No token configured is not "everyone is an admin": the surface closes, as the runner's does."""
    monkeypatch.delenv(ADMIN_TOKEN_ENV_VAR, raising=False)

    response = connected.post("/api/v1/admin/freeze", json={}, headers=TOKEN_HEADERS)

    assert response.status_code == 503, response.text
    assert ADMIN_TOKEN_ENV_VAR in response.json()["detail"]
    assert world.audit_rows() == []


def test_a_wrong_or_missing_token_is_refused(connected: TestClient, world: World) -> None:
    for headers in (
        {},
        {"X-ProcessLens-Admin-Token": "not-the-token"},
        {"X-ProcessLens-Admin-Token": ""},
        {"X-ProcessLens-Admin-Token": "admin-token-for-test"},
    ):
        response = connected.post("/api/v1/admin/freeze", json={}, headers=headers)
        assert response.status_code == 403, f"{headers}: {response.text}"

    assert world.audit_rows() == [], "nothing was written for a refused caller"


def test_the_environment_variables_own_name_is_accepted_as_a_header(
    connected: TestClient, world: World
) -> None:
    """A caller that spells the header exactly as the ticket does still gets in."""
    response = connected.post(
        "/api/v1/admin/freeze",
        json={"tenant_id": str(world.tenant_id)},
        headers={ADMIN_TOKEN_ENV_VAR: TOKEN},
    )

    assert response.status_code == 200, response.text
    assert response.json()["runs_frozen"] is True


# --- freeze and unfreeze ----------------------------------------------------


def test_freezing_writes_the_flag_and_one_audit_row_together(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    actor = world.person(role="owner")
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(actor))

    response = connected.post(
        "/api/v1/admin/freeze",
        json={"tenant_id": str(world.tenant_id), "reason": "incident"},
        headers=TOKEN_HEADERS,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runs_frozen"] is True and body["runs_frozen_at"] is not None
    assert body["runs_frozen_by"] == str(actor)
    assert world.audit_rows() == [("freeze_runs", "core", str(world.tenant_id))]
    with world.conn.cursor() as cur:
        cur.execute(
            "SELECT actor_user_id, module, action, detail FROM admin_audit WHERE tenant_id = %s",
            (world.tenant_id,),
        )
        assert cur.fetchone() == (actor, "core", "freeze_runs", {"reason": "incident"})


def test_unfreezing_clears_it_and_writes_its_own_row(connected: TestClient, world: World) -> None:
    freeze_runs(world.conn, world.tenant_id)

    response = connected.post(
        "/api/v1/admin/unfreeze", json={"tenant_id": str(world.tenant_id)}, headers=TOKEN_HEADERS
    )

    assert response.status_code == 200, response.text
    assert response.json()["runs_frozen"] is False
    assert response.json()["runs_frozen_at"] is None
    assert world.audit_rows() == [("unfreeze_runs", "core", str(world.tenant_id))]


def test_freezing_a_tenant_that_does_not_exist_is_a_404(connected: TestClient, world: World) -> None:
    missing = uuid.uuid4()

    response = connected.post(
        "/api/v1/admin/freeze", json={"tenant_id": str(missing)}, headers=TOKEN_HEADERS
    )

    assert response.status_code == 404, response.text
    assert str(missing) in response.json()["detail"]
    assert world.audit_rows() == []


def test_the_admin_endpoints_are_the_only_ones_needing_the_token(
    connected: TestClient, world: World
) -> None:
    """The page's own reads keep working without it; only the admin surface holds the token."""
    assert connected.get("/api/v1/tasks").status_code == 200


# --- the gate on starting a run ---------------------------------------------


def test_starting_a_run_while_frozen_is_refused_with_the_products_sentence(
    connected: TestClient, world: World
) -> None:
    world.add_input("a@example.com")
    freeze_runs(world.conn, world.tenant_id)

    response = connected.post(
        "/api/v1/runs",
        json={
            "workflow_id": str(world.workflow_id),
            "recipe_version_id": str(world.recipe_version_id),
            "input_batch_id": str(world.batch_id),
            "mode": "batch",
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == RUNS_FROZEN_DETAIL
    with world.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM runs WHERE tenant_id = %s", (world.tenant_id,))
        assert cur.fetchone()[0] == 0, "nothing was written"


def test_resuming_a_run_while_frozen_is_refused(connected: TestClient, world: World) -> None:
    world.add_input("a@example.com")
    run_id = world.create_run()
    world.conn.execute("UPDATE runs SET status = 'paused' WHERE id = %s", (run_id,))
    freeze_runs(world.conn, world.tenant_id)

    response = connected.post(f"/api/v1/runs/{run_id}/resume")

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == RUNS_FROZEN_DETAIL
    with world.conn.cursor() as cur:
        cur.execute("SELECT status FROM runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] == "paused", "the run kept its state"


def test_a_run_starts_again_once_the_freeze_is_lifted(connected: TestClient, world: World) -> None:
    world.add_input("a@example.com")
    freeze_runs(world.conn, world.tenant_id)
    connected.post(
        "/api/v1/admin/unfreeze", json={"tenant_id": str(world.tenant_id)}, headers=TOKEN_HEADERS
    )

    assert world.create_run()


def test_a_run_that_does_not_exist_is_still_a_404_while_frozen(
    connected: TestClient, world: World
) -> None:
    freeze_runs(world.conn, world.tenant_id)

    response = connected.post(f"/api/v1/runs/{uuid.uuid4()}/resume")

    assert response.status_code == 404, response.text


# --- the extension floor ----------------------------------------------------


def _set_floor(client: TestClient, world: World, version: str | None) -> Any:
    return client.post(
        "/api/v1/admin/min-extension-version",
        json={"tenant_id": str(world.tenant_id), "version": version},
        headers=TOKEN_HEADERS,
    )


def test_the_floor_is_set_cleared_and_audited(connected: TestClient, world: World) -> None:
    set_response = _set_floor(connected, world, "1.10.0")

    assert set_response.status_code == 200, set_response.text
    assert set_response.json()["min_extension_version"] == "1.10.0"

    clear_response = _set_floor(connected, world, None)

    assert clear_response.status_code == 200, clear_response.text
    assert clear_response.json()["min_extension_version"] is None
    assert world.audit_rows() == [
        ("set_min_extension_version", "core", str(world.tenant_id)),
        ("set_min_extension_version", "core", str(world.tenant_id)),
    ]


def test_a_malformed_floor_is_refused_before_anything_is_stored(
    connected: TestClient, world: World
) -> None:
    for bad in ("", "   ", "one.two", "1.2.3.4.5", "1..0", "-1", "1.2.x"):
        response = _set_floor(connected, world, bad)
        assert response.status_code == 422, f"{bad!r}: {response.text}"

    assert world.audit_rows() == []
    with world.conn.cursor() as cur:
        cur.execute(
            "SELECT min_extension_version FROM tenants WHERE id = %s", (world.tenant_id,)
        )
        assert cur.fetchone()[0] is None


def test_an_old_extension_is_refused_for_starting_and_resuming(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    person = world.person()
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(person))
    world.add_input("a@example.com")
    run_id = world.create_run()
    world.conn.execute("UPDATE runs SET status = 'paused' WHERE id = %s", (run_id,))
    _set_floor(connected, world, "1.10.0")
    connected.post(
        "/api/v1/executor/version",
        json={"version": "1.9.2"},
        headers={"X-ProcessLens-Client": "extension"},
    )

    start = connected.post(
        "/api/v1/runs",
        json={
            "workflow_id": str(world.workflow_id),
            "recipe_version_id": str(world.recipe_version_id),
            "input_batch_id": str(world.batch_id),
            "mode": "batch",
        },
    )
    resume = connected.post(f"/api/v1/runs/{run_id}/resume")

    for response in (start, resume):
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == EXTENSION_FLOOR_TEMPLATE.format(version="1.10.0")


def test_an_up_to_date_extension_may_start_one(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    person = world.person()
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(person))
    world.add_input("a@example.com")
    _set_floor(connected, world, "1.10.0")
    connected.post(
        "/api/v1/executor/version",
        json={"version": "1.10.0"},
        headers={"X-ProcessLens-Client": "extension"},
    )

    assert world.create_run(), "1.10.0 is not below 1.10.0"


def test_a_person_who_has_never_reported_is_not_below_the_floor(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    """"Not seen yet" is not "out of date": the floor is compared against a report that exists."""
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person()))
    world.add_input("a@example.com")
    _set_floor(connected, world, "99.0.0")

    assert world.create_run()


def test_a_new_floor_does_not_touch_a_run_already_going(
    connected: TestClient, world: World
) -> None:
    world.add_input("a@example.com")
    run_id = world.create_run()

    assert _set_floor(connected, world, "99.0.0").status_code == 200

    with world.conn.cursor() as cur:
        cur.execute("SELECT status FROM runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] == "queued", "the run is exactly where it was"


# --- a person whose access is off -------------------------------------------


def test_a_deactivated_person_cannot_start_or_resume_a_run(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    person = world.person(deactivated=True)
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(person))
    world.add_input("a@example.com")
    # A run that exists to resume, created while the person was still active.
    world.conn.execute("UPDATE users SET deactivated_at = NULL WHERE id = %s", (person,))
    run_id = world.create_run()
    world.conn.execute(
        "UPDATE runs SET status = 'paused', pause_reason = 'paused_by_operator' WHERE id = %s",
        (run_id,),
    )
    world.conn.execute("UPDATE users SET deactivated_at = now() WHERE id = %s", (person,))

    start = connected.post(
        "/api/v1/runs",
        json={
            "workflow_id": str(world.workflow_id),
            "recipe_version_id": str(world.recipe_version_id),
            "input_batch_id": str(world.batch_id),
            "mode": "batch",
        },
    )
    resume = connected.post(f"/api/v1/runs/{run_id}/resume")

    for response in (start, resume):
        assert response.status_code == 403, response.text
        assert "turned off" in response.json()["detail"]
    with world.conn.cursor() as cur:
        cur.execute("SELECT status FROM runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] == "paused", "their open run kept its state"


def test_a_deactivated_person_cannot_approve(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    """A decision is refused; nothing about the run the decision belonged to is changed."""
    person = world.person(role="reviewer", deactivated=True)

    response = connected.post(
        "/api/v1/approvals",
        json={
            "request_id": str(uuid.uuid4()),
            "decision": "approved",
            "decided_by": str(person),
        },
    )

    assert response.status_code == 403, response.text
    assert "turned off" in response.json()["detail"]


# --- the workspace state the page reads -------------------------------------


def test_the_workspace_state_reports_the_freeze_and_the_floor(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    person = world.person()
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(person))
    _set_floor(connected, world, "1.10.0")
    connected.post(
        "/api/v1/executor/version",
        json={"version": "1.9.2"},
        headers={"X-ProcessLens-Client": "extension"},
    )
    freeze_runs(world.conn, world.tenant_id)

    response = connected.get("/api/v1/workspace/state")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "tenant_id": str(world.tenant_id),
        "runs_frozen": True,
        "min_extension_version": "1.10.0",
        "extension_version": "1.9.2",
        "extension_out_of_date": True,
    }


def test_the_workspace_state_says_nothing_is_wrong_by_default(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person()))

    body = connected.get("/api/v1/workspace/state").json()

    assert body["runs_frozen"] is False
    assert body["min_extension_version"] is None
    assert body["extension_version"] is None
    assert body["extension_out_of_date"] is False


def test_the_workspace_state_takes_a_tenant_filter(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    """A caller that knows the company it is asking about does not need an operator at all."""
    monkeypatch.delenv(OPERATOR_ENV_VAR, raising=False)
    freeze_runs(world.conn, world.tenant_id)

    response = connected.get(f"/api/v1/workspace/state?tenant_id={world.tenant_id}")

    assert response.status_code == 200, response.text
    assert response.json()["runs_frozen"] is True
    assert response.json()["extension_version"] is None


def test_the_workspace_state_refuses_when_there_is_nobody_to_ask_about(
    monkeypatch: MonkeyPatch, connected: TestClient
) -> None:
    monkeypatch.delenv(OPERATOR_ENV_VAR, raising=False)

    response = connected.get("/api/v1/workspace/state")

    assert response.status_code == 503, response.text
    assert OPERATOR_ENV_VAR in response.json()["detail"]


def test_the_workspace_state_refuses_an_unknown_tenant(connected: TestClient) -> None:
    response = connected.get(f"/api/v1/workspace/state?tenant_id={uuid.uuid4()}")

    assert response.status_code == 404, response.text


# --- the extension reports its own version ----------------------------------


def test_the_extension_version_is_stored_against_the_operator(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World, conn: psycopg.Connection
) -> None:
    person = world.person()
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(person))

    first = connected.post(
        "/api/v1/executor/version",
        json={"version": "1.9.2"},
        headers={"X-ProcessLens-Client": "extension"},
    )
    second = connected.post(
        "/api/v1/executor/version",
        json={"version": "1.10.0"},
        headers={"X-ProcessLens-Client": "extension"},
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    report = get_extension_report(conn, person)
    assert report is not None
    assert (report.user_id, report.tenant_id, report.version) == (
        person,
        world.tenant_id,
        "1.10.0",
    )
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM extension_reports WHERE user_id = %s", (person,))
        assert cur.fetchone()[0] == 1, "one row per person, holding the newest report"


def test_a_malformed_extension_version_is_refused(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    person = world.person()
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(person))

    for bad in ("", "later", "1.x", "1.2.3.4.5"):
        response = connected.post(
            "/api/v1/executor/version",
            json={"version": bad},
            headers={"X-ProcessLens-Client": "extension"},
        )
        assert response.status_code == 422, f"{bad!r}: {response.text}"

    with world.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM extension_reports WHERE user_id = %s", (person,))
        assert cur.fetchone()[0] == 0


def test_only_the_extension_may_report_a_version(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person()))

    response = connected.post("/api/v1/executor/version", json={"version": "1.9.2"})

    assert response.status_code == 403, response.text


def test_a_version_cannot_be_reported_without_an_operator(
    monkeypatch: MonkeyPatch, connected: TestClient
) -> None:
    monkeypatch.delenv(OPERATOR_ENV_VAR, raising=False)

    response = connected.post(
        "/api/v1/executor/version",
        json={"version": "1.9.2"},
        headers={"X-ProcessLens-Client": "extension"},
    )

    assert response.status_code == 503, response.text
    assert OPERATOR_ENV_VAR in response.json()["detail"]


def test_a_version_for_an_id_that_names_nobody_is_refused(
    monkeypatch: MonkeyPatch, connected: TestClient
) -> None:
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(uuid.uuid4()))

    response = connected.post(
        "/api/v1/executor/version",
        json={"version": "1.9.2"},
        headers={"X-ProcessLens-Client": "extension"},
    )

    assert response.status_code == 403, response.text
    assert OPERATOR_ENV_VAR in response.json()["detail"]


# --- the front door lets the extension's own origin through this one path ----


def test_the_extension_origin_may_report_a_version(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    extension_id = "abcdefghijklmnopabcdefghijklmnop"
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", extension_id)
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person()))

    response = connected.post(
        "/api/v1/executor/version",
        json={"version": "1.9.2"},
        headers={
            "origin": f"chrome-extension://{extension_id}",
            "X-ProcessLens-Client": "extension",
        },
    )

    assert response.status_code == 201, response.text


def test_a_foreign_origin_may_not_report_a_version(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", "abcdefghijklmnopabcdefghijklmnop")
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person()))

    response = connected.post(
        "/api/v1/executor/version",
        json={"version": "1.9.2"},
        headers={"origin": "http://evil.example", "X-ProcessLens-Client": "extension"},
    )

    assert response.status_code == 403, response.text
    assert "origin" in response.json()["detail"]


def test_another_extension_id_may_not_report_a_version(
    monkeypatch: MonkeyPatch, connected: TestClient, world: World
) -> None:
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", "abcdefghijklmnopabcdefghijklmnop")
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person()))

    response = connected.post(
        "/api/v1/executor/version",
        json={"version": "1.9.2"},
        headers={
            "origin": "chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "X-ProcessLens-Client": "extension",
        },
    )

    assert response.status_code == 403, response.text


def test_the_environment_is_never_read_from_a_client_supplied_value(
    connected: TestClient, world: World
) -> None:
    """The token comes from the environment; a request cannot name its own."""
    assert os.environ.get(ADMIN_TOKEN_ENV_VAR) == TOKEN
    response = connected.post(
        "/api/v1/admin/freeze",
        json={"tenant_id": str(world.tenant_id), "token": TOKEN},
        headers={"X-ProcessLens-Admin-Token": "wrong"},
    )
    assert response.status_code == 403, response.text
