"""PL-112 item 4: a sign-in task is marked as one, and its one-row run carries the picked email.

The marker lives on the task (``workflows.sign_in`` + ``sign_in_column``); the email itself is not
a second copy on the server -- it is the teaching example already in the recipe. The run-once route
accepts one row of values so the panel can run the task without a spreadsheet.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from tests.api.test_batches_api import add_task, recipe_document
from tests.db.seed import Ledger

ENV_VAR = "PROCESSLENS_OPERATOR_ID"
ORIGIN = "http://127.0.0.1:8123"


def sign_in_document() -> dict:
    """A read-only sign-in: fill the email, press Continue, and nothing is saved."""
    return {
        "format_version": 1,
        "meta": {
            "name": "Sign in to the app",
            "when_to_use": "The account needs a code from its email before it shows its pages.",
            "input_schema": {
                "fields": {
                    "email": {"type": "string", "required": True, "example": "a@example.com"}
                }
            },
            "record_key": "{{row.email | lower}}",
            "idempotency_key": "{{row.email | lower}}",
            "approval_policy": {"commit": "none"},
            "allowed_origins": [ORIGIN],
        },
        "steps": [
            {"id": "open", "action": "navigate", "url": f"{ORIGIN}/login"},
            {
                "id": "fill-email",
                "action": "fill",
                "targets": [{"by": "label", "text": "Email"}],
                "value": {"kind": "input", "column": "email"},
            },
            {
                "id": "continue",
                "action": "click",
                "targets": [{"by": "role", "role": "button", "name": "Continue"}],
            },
        ],
    }


@pytest.fixture
def operator(connected: TestClient, ledger: Ledger, monkeypatch: MonkeyPatch) -> str:
    user_id = ledger.add_user(role="owner", name="Sign-in owner", email="owner@signin.test")
    monkeypatch.setenv(ENV_VAR, str(user_id))
    return str(user_id)


def test_a_saved_sign_in_task_is_marked_with_its_email_column(
    connected: TestClient, ledger: Ledger, operator: str
) -> None:
    response = connected.post(
        "/api/v1/tasks",
        json={
            "name": "Sign in to the app",
            "recipe": sign_in_document(),
            "sign_in": True,
            "sign_in_column": "email",
        },
    )

    assert response.status_code == 201, response.text
    listed = connected.get(f"/api/v1/tasks?tenant_id={ledger.tenant_id}")
    task = next(
        row for row in listed.json() if row["workflow_id"] == response.json()["workflow_id"]
    )
    assert task["sign_in"] is True
    assert task["sign_in_column"] == "email"
    assert task["sign_in_email"] == "a@example.com"


def test_reteaching_updates_the_sign_in_marker(
    connected: TestClient, ledger: Ledger, operator: str
) -> None:
    created = connected.post(
        "/api/v1/tasks",
        json={
            "name": "Sign in to the app",
            "recipe": sign_in_document(),
            "sign_in": True,
            "sign_in_column": "email",
        },
    )
    assert created.status_code == 201, created.text

    taught_again = connected.post(
        "/api/v1/tasks",
        json={"name": "Sign in to the app", "recipe": sign_in_document()},
    )

    assert taught_again.status_code == 201, taught_again.text
    row = connected.get(f"/api/v1/tasks?tenant_id={ledger.tenant_id}").json()
    task = next(item for item in row if item["workflow_id"] == created.json()["workflow_id"])
    assert task["sign_in"] is False
    assert task["sign_in_column"] is None


def test_run_once_puts_the_picked_email_in_the_taught_column(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, operator: str
) -> None:
    created = connected.post(
        "/api/v1/tasks",
        json={
            "name": "Sign in to the app",
            "recipe": sign_in_document(),
            "sign_in": True,
            "sign_in_column": "email",
        },
    )
    workflow_id = created.json()["workflow_id"]

    started = connected.post(
        f"/api/v1/tasks/{workflow_id}/run-once",
        json={"values": {"email": "picked@example.com"}},
    )

    assert started.status_code == 201, started.text
    run_id = uuid.UUID(started.json()["run_id"])
    batch_id = conn.execute("SELECT input_batch_id FROM runs WHERE id = %s", (run_id,)).fetchone()[
        0
    ]
    row = conn.execute(
        "SELECT clean ->> 'email', record_key FROM input_records WHERE batch_id = %s",
        (batch_id,),
    ).fetchone()
    assert row[0] == "picked@example.com"
    assert row[1] != "picked@example.com", "each Run once is new work, not a repeated key"


def test_run_once_still_refuses_a_spreadsheet_task_with_no_values(
    connected: TestClient, ledger: Ledger, operator: str
) -> None:
    created = connected.post(
        "/api/v1/tasks",
        json={
            "name": "Sign in to the app",
            "recipe": sign_in_document(),
            "sign_in": True,
            "sign_in_column": "email",
        },
    )

    response = connected.post(f"/api/v1/tasks/{created.json()['workflow_id']}/run-once", json={})

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "This task needs a spreadsheet."


def test_a_run_page_can_say_why_the_code_read_failed(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    conn.execute(
        "UPDATE runs SET status = 'paused', pause_reason = 'code_required', "
        "pause_detail = 'none' WHERE id = %s",
        (run.id,),
    )

    response = connected.get(f"/api/v1/runs/{run.id}")

    assert response.status_code == 200, response.text
    assert response.json()["pause_detail"] == "none"


# ------------------------------------------------------------------ PL-112, T1 review
#
# ``run-once`` builds its row with a fresh ``run_once_id``, so the row's record key can never
# repeat and the committed-keys guard has nothing to compare. That is right for a sign-in task
# (running it twice IS two pieces of work) and wrong for anything that saves: a task with inputs
# belongs in the normal run flow, where the key comes from the spreadsheet. So ``values`` is only
# accepted for a sign-in task, and then only the one column and one address it needs.


def a_sign_in_task(connected: TestClient) -> str:
    """A saved sign-in task, marked as one, and its workflow id as a string."""
    created = connected.post(
        "/api/v1/tasks",
        json={
            "name": "Sign in to the app",
            "recipe": sign_in_document(),
            "sign_in": True,
            "sign_in_column": "email",
        },
    )
    assert created.status_code == 201, created.text
    return str(created.json()["workflow_id"])


def test_run_once_refuses_a_row_for_a_task_that_saves(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, operator: str
) -> None:
    """A task with spreadsheet columns keeps the spreadsheet path: its key must come from the
    file, not from a fresh id that would defeat the duplicate guard.
    """
    workflow_id, _ = add_task(conn, ledger.tenant_id, name="Add inquiries")

    response = connected.post(
        f"/api/v1/tasks/{workflow_id}/run-once",
        json={"values": {"email": "someone@example.com"}},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "This task runs from a spreadsheet, not from one row."
    written = conn.execute(
        "SELECT count(*) FROM input_batches WHERE workflow_id = %s", (workflow_id,)
    ).fetchone()[0]
    assert written == 0, "a refused run writes no batch"


def test_run_once_refuses_a_row_for_a_task_that_saves_nothing_but_was_not_taught_as_a_sign_in(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, operator: str
) -> None:
    """The rule is the marker, not the shape: a read-only task without it takes no row."""
    workflow_id, _ = add_task(
        conn,
        ledger.tenant_id,
        document=recipe_document(
            fields={"run_once_id": {"type": "string", "example": "x"}},
            record_key="{{row.run_once_id}}",
        ),
        name="Close the day",
    )

    response = connected.post(
        f"/api/v1/tasks/{workflow_id}/run-once",
        json={"values": {"email": "someone@example.com"}},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "This task runs from a spreadsheet, not from one row."


def test_run_once_refuses_a_row_in_the_wrong_column(connected: TestClient, operator: str) -> None:
    workflow_id = a_sign_in_task(connected)

    response = connected.post(
        f"/api/v1/tasks/{workflow_id}/run-once",
        json={"values": {"name": "someone@example.com"}},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Run this task with one email address, in its own field."


def test_run_once_refuses_a_row_with_an_extra_column(connected: TestClient, operator: str) -> None:
    workflow_id = a_sign_in_task(connected)

    response = connected.post(
        f"/api/v1/tasks/{workflow_id}/run-once",
        json={"values": {"email": "someone@example.com", "name": "Someone"}},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Run this task with one email address, in its own field."


def test_run_once_refuses_an_empty_or_non_string_email(
    connected: TestClient, operator: str
) -> None:
    workflow_id = a_sign_in_task(connected)

    for value in ("", "   ", 42, None):
        response = connected.post(
            f"/api/v1/tasks/{workflow_id}/run-once",
            json={"values": {"email": value}},
        )
        assert response.status_code == 422, (value, response.text)
        assert response.json()["detail"] == "Type the email this run should use.", value


def test_run_once_refuses_an_email_longer_than_the_column_holds(
    connected: TestClient, operator: str
) -> None:
    workflow_id = a_sign_in_task(connected)

    response = connected.post(
        f"/api/v1/tasks/{workflow_id}/run-once",
        json={"values": {"email": f"{'a' * 315}@x.test"}},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "That email is too long to use here."


def test_run_once_trims_the_email_it_is_given(
    connected: TestClient, conn: psycopg.Connection, operator: str
) -> None:
    workflow_id = a_sign_in_task(connected)

    started = connected.post(
        f"/api/v1/tasks/{workflow_id}/run-once",
        json={"values": {"email": "  picked@example.com  "}},
    )

    assert started.status_code == 201, started.text
    run_id = uuid.UUID(started.json()["run_id"])
    batch_id = conn.execute("SELECT input_batch_id FROM runs WHERE id = %s", (run_id,)).fetchone()[
        0
    ]
    stored = conn.execute(
        "SELECT clean ->> 'email' FROM input_records WHERE batch_id = %s", (batch_id,)
    ).fetchone()[0]
    assert stored == "picked@example.com"
