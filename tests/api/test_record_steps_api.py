"""PL-065: ``GET /api/v1/runs/{run_id}/records/{record_id}/steps``.

The results page opens one row and sees what happened inside it: each step's name (from the recipe
version the run is pinned to), how many attempts it took, how many seconds it ran, and -- for the
step that stopped the row -- one plain sentence.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.db.seed import DESTINATION_KEY, Ledger

BASE = datetime(2026, 10, 20, 9, 0, 0)

# A recipe with a branch, so a step nested inside it has a name too.
STEPS = [
    {
        "id": "open-form",
        "action": "navigate",
        "name": "Open the new lead form",
        "url": "http://localhost:5173/leads/new",
    },
    {
        "id": "type-email",
        "action": "fill",
        "name": "Email",
        "targets": [{"by": "label", "text": "Email"}],
        "value": {"kind": "input", "column": "email"},
    },
    {
        "id": "already-there",
        "action": "branch",
        "name": "A lead for this email already exists",
        "when": {"text_visible": "Lead found"},
        "then": [
            {
                "id": "skip-existing",
                "action": "end_record",
                "outcome": "skipped",
                "reason": "Already there.",
            }
        ],
    },
    {
        "id": "click-save",
        "action": "click",
        "name": "Save the lead",
        "commit": True,
        "targets": [{"by": "role", "role": "button", "name": "Save"}],
        "assert_after": {"text_visible": "Lead created", "timeout_ms": 8000},
    },
]


@pytest.fixture
def run_pinned_to_a_named_recipe(
    conn: psycopg.Connection, ledger: Ledger
) -> tuple[uuid.UUID, uuid.UUID]:
    """A run pinned to version 2 of the task, whose recipe names each step."""
    version_id = conn.execute(
        """
        INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason)
        VALUES (%s, %s, 2, %s, 'named steps for the test') RETURNING id
        """,
        (
            ledger.tenant_id,
            ledger.workflow_id,
            json.dumps({"format_version": 1, "meta": {}, "steps": STEPS}),
        ),
    ).fetchone()[0]
    batch_id = conn.execute(
        "INSERT INTO input_batches (tenant_id, workflow_id, kind) "
        "VALUES (%s, %s, 'csv') RETURNING id",
        (ledger.tenant_id, ledger.workflow_id),
    ).fetchone()[0]
    run_id = conn.execute(
        """
        INSERT INTO runs (tenant_id, workflow_id, recipe_version_id, input_batch_id, mode, status,
                          destination_key, started_at, finished_at)
        VALUES (%s, %s, %s, %s, 'batch', 'completed', %s, now(), now()) RETURNING id
        """,
        (ledger.tenant_id, ledger.workflow_id, version_id, batch_id, DESTINATION_KEY),
    ).fetchone()[0]
    return run_id, version_id


def _record(conn: psycopg.Connection, ledger: Ledger, run_id: uuid.UUID, key: str) -> uuid.UUID:
    batch_id = conn.execute("SELECT input_batch_id FROM runs WHERE id = %s", (run_id,)).fetchone()[
        0
    ]
    input_id = conn.execute(
        """
        INSERT INTO input_records (tenant_id, batch_id, row_index, raw, record_key, status)
        VALUES (%s, %s, 1, '{}'::jsonb, %s, 'ready') RETURNING id
        """,
        (ledger.tenant_id, batch_id, key),
    ).fetchone()[0]
    return conn.execute(
        """
        INSERT INTO run_records (tenant_id, run_id, input_record_id, record_key, state,
                                 attempts, max_attempts, idempotency_key)
        VALUES (%s, %s, %s, %s, 'pending', 0, 3, %s) RETURNING id
        """,
        (ledger.tenant_id, run_id, input_id, key, key),
    ).fetchone()[0]


def _step(
    conn: psycopg.Connection,
    ledger: Ledger,
    record_id: uuid.UUID,
    *,
    step_id: str,
    outcome: str = "ok",
    attempt: int = 1,
    starts_after: float = 0.0,
    takes: float | None = 0.4,
    error: object = None,
) -> None:
    started = BASE + timedelta(seconds=starts_after)
    conn.execute(
        """
        INSERT INTO step_events
          (tenant_id, run_record_id, step_id, attempt, started_at, ended_at, outcome,
           observed, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            ledger.tenant_id,
            record_id,
            step_id,
            attempt,
            started,
            None if takes is None else started + timedelta(seconds=takes),
            outcome,
            json.dumps({"value": "never shown"}),
            json.dumps(error) if error is not None else None,
        ),
    )


def test_one_row_answers_its_steps_in_order(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, run_pinned_to_a_named_recipe
) -> None:
    run_id, _ = run_pinned_to_a_named_recipe
    record = _record(conn, ledger, run_id, "priya@example.com")
    _step(conn, ledger, record, step_id="open-form", takes=0.3)
    _step(conn, ledger, record, step_id="type-email", starts_after=0.3, takes=0.4)

    response = connected.get(f"/api/v1/runs/{run_id}/records/{record}/steps")

    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "step_id": "open-form",
            "step_name": "Open the new lead form",
            "attempt": 1,
            "outcome": "ok",
            "seconds": 0.3,
            "error": None,
        },
        {
            "step_id": "type-email",
            "step_name": "Email",
            "attempt": 1,
            "outcome": "ok",
            "seconds": 0.4,
            "error": None,
        },
    ]


def test_a_step_nested_in_a_branch_is_named_or_falls_back_to_its_id(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, run_pinned_to_a_named_recipe
) -> None:
    run_id, _ = run_pinned_to_a_named_recipe
    record = _record(conn, ledger, run_id, "second@example.com")
    _step(conn, ledger, record, step_id="skip-existing", outcome="skipped", takes=0.1)

    body = connected.get(f"/api/v1/runs/{run_id}/records/{record}/steps").json()

    # The nested step has no `name` of its own, so its id is the fallback -- and it is found at all,
    # which a walk that stopped at the top level would miss.
    assert body[0]["step_name"] == "skip-existing"


def test_a_nested_step_with_a_name_uses_it(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, run_pinned_to_a_named_recipe
) -> None:
    run_id, _ = run_pinned_to_a_named_recipe
    record = _record(conn, ledger, run_id, "nested@example.com")
    _step(conn, ledger, record, step_id="already-there")
    _step(conn, ledger, record, step_id="skip-existing", starts_after=0.2, outcome="skipped")

    body = connected.get(f"/api/v1/runs/{run_id}/records/{record}/steps").json()

    assert [line["step_name"] for line in body] == [
        "A lead for this email already exists",
        "skip-existing",
    ]


def test_a_step_still_running_has_no_seconds(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, run_pinned_to_a_named_recipe
) -> None:
    run_id, _ = run_pinned_to_a_named_recipe
    record = _record(conn, ledger, run_id, "running@example.com")
    _step(conn, ledger, record, step_id="click-save", takes=None)

    body = connected.get(f"/api/v1/runs/{run_id}/records/{record}/steps").json()

    assert body[0]["seconds"] is None


def test_seconds_are_rounded_to_a_tenth(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, run_pinned_to_a_named_recipe
) -> None:
    run_id, _ = run_pinned_to_a_named_recipe
    record = _record(conn, ledger, run_id, "slow@example.com")
    _step(conn, ledger, record, step_id="type-email", takes=1.234)

    body = connected.get(f"/api/v1/runs/{run_id}/records/{record}/steps").json()

    assert body[0]["seconds"] == 1.2


def test_the_step_that_stopped_the_row_carries_its_sentence(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, run_pinned_to_a_named_recipe
) -> None:
    run_id, _ = run_pinned_to_a_named_recipe
    record = _record(conn, ledger, run_id, "stopped@example.com")
    _step(
        conn,
        ledger,
        record,
        step_id="click-save",
        outcome="failed",
        attempt=2,
        starts_after=1.0,
        takes=0.9,
        error={"message": "the Save button never appeared", "detail": {"selector": "#save"}},
    )

    body = connected.get(f"/api/v1/runs/{run_id}/records/{record}/steps").json()

    assert body[0]["outcome"] == "failed"
    assert body[0]["error"] == "the Save button never appeared"
    assert body[0]["attempt"] == 2
    # The error's detail -- where the button was looked for -- is not the page's business.
    assert "selector" not in str(body)


def test_a_row_with_no_steps_is_an_empty_list(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, run_pinned_to_a_named_recipe
) -> None:
    run_id, _ = run_pinned_to_a_named_recipe
    record = _record(conn, ledger, run_id, "untouched@example.com")

    response = connected.get(f"/api/v1/runs/{run_id}/records/{record}/steps")

    assert response.status_code == 200
    assert response.json() == []


def test_a_record_in_another_run_is_a_404(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, run_pinned_to_a_named_recipe
) -> None:
    run_id, _ = run_pinned_to_a_named_recipe
    other = ledger.new_run()
    theirs = ledger.add_record(other)
    _step(conn, ledger, theirs, step_id="open-form")

    response = connected.get(f"/api/v1/runs/{run_id}/records/{theirs}/steps")

    assert response.status_code == 404, response.text


def test_a_record_that_does_not_exist_is_a_404(
    connected: TestClient, ledger: Ledger, run_pinned_to_a_named_recipe
) -> None:
    run_id, _ = run_pinned_to_a_named_recipe

    response = connected.get(f"/api/v1/runs/{run_id}/records/{uuid.uuid4()}/steps")

    assert response.status_code == 404, response.text


def test_a_run_that_does_not_exist_is_a_404(connected: TestClient, ledger: Ledger) -> None:
    response = connected.get(f"/api/v1/runs/{uuid.uuid4()}/records/{uuid.uuid4()}/steps")

    assert response.status_code == 404, response.text


def test_another_tenants_record_is_a_404(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, run_pinned_to_a_named_recipe
) -> None:
    """A record whose tenant is not the run's tenant is refused, not read."""
    run_id, _ = run_pinned_to_a_named_recipe
    theirs = Ledger(conn)
    other_run = theirs.new_run()
    record = theirs.add_record(other_run)
    _step(conn, theirs, record, step_id="open-form")

    response = connected.get(f"/api/v1/runs/{run_id}/records/{record}/steps")

    assert response.status_code == 404, response.text
