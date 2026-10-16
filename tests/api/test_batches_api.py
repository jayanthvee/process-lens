"""Adding a spreadsheet, checking it against a task, and saving the mapping.

The style is ``tests/api/test_home_api.py``'s: the ``ledger`` fixture builds a fresh tenant, so
every assertion is scoped to this test's own data in the shared session database. Each test lays
down its own task (a workflow and one recipe version) because the recipes here carry the
``input_schema`` the check reads.
"""

from __future__ import annotations

import io
import json
import uuid

import psycopg
from fastapi.testclient import TestClient
from openpyxl import Workbook

from tests.db.seed import Ledger

ORIGIN = "http://localhost:5173"
MEGABYTE = 1024 * 1024
FILE_NAME = "customer-inquiries.csv"
REASON_NOT_IN = "\u201cNonsense\u201d isn't one of the answers this task knows."


def recipe_document(
    *,
    fields: dict | None = None,
    rules: list[dict] | None = None,
    record_key: str = "{{row.email}}",
) -> dict:
    """A runnable task document with the columns and precheck rules a test cares about."""
    schema: dict = {
        "fields": fields or {"email": {"type": "string", "required": True}},
    }
    if rules is not None:
        schema["rules"] = rules
    return {
        "format_version": 1,
        "meta": {
            "name": "Add customer inquiries to Odoo CRM",
            "when_to_use": "Each inquiry must be added once.",
            "input_schema": schema,
            "record_key": record_key,
            "idempotency_key": record_key,
            "approval_policy": {"commit": "none"},
            "allowed_origins": [ORIGIN],
        },
        "steps": [
            {"id": "open", "action": "navigate", "url": f"{ORIGIN}/leads/new"},
            {
                "id": "save",
                "action": "click",
                "commit": True,
                "assert_after": {"text_visible": "Lead created", "timeout_ms": 8000},
                "targets": [{"by": "role", "role": "button", "name": "Save"}],
            },
        ],
    }


def add_task(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    *,
    document: dict | None = None,
    name: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """One task: a workflow and its one recipe version.

    The name is unique per call, because a tenant's workflows are unique by name and the ``ledger``
    fixture already has one.
    """
    name = name or f"Inquiries {uuid.uuid4().hex[:6]}"
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, %s, 'demo-crm', %s) RETURNING id",
            (tenant_id, name, [ORIGIN]),
        )
        workflow_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, 1, %s, 'compiled from recording') RETURNING id",
            (tenant_id, workflow_id, json.dumps(document or recipe_document())),
        )
        return workflow_id, cur.fetchone()[0]


def csv_bytes(rows: list[list[str]]) -> bytes:
    """A CSV file, as a person's spreadsheet would be."""
    return ("\n".join(",".join(row) for row in rows) + "\n").encode("utf-8")


def xlsx_bytes(rows: list[list[str]]) -> bytes:
    """An XLSX file, built here so the upload's reader is the thing under test."""
    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def upload(
    client: TestClient,
    workflow_id: uuid.UUID,
    content: bytes,
    *,
    filename: str = FILE_NAME,
) -> object:
    """`POST /api/v1/batches`, as multipart."""
    return client.post(
        "/api/v1/batches",
        data={"workflow_id": str(workflow_id)},
        files={"file": (filename, content, "application/octet-stream")},
    )


def stored_rows(conn: psycopg.Connection, batch_id: str) -> list[dict]:
    """The batch's rows as the database holds them, in row order."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT row_index, raw, clean, record_key, status, issues FROM input_records "
            "WHERE batch_id = %s ORDER BY row_index",
            (batch_id,),
        )
        columns = ["row_index", "raw", "clean", "record_key", "status", "issues"]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def check(
    client: TestClient,
    batch_id: str,
    recipe_version_id: uuid.UUID,
    *,
    sample_row: dict | None = None,
) -> dict:
    """`GET /api/v1/batches/{id}/check`, and its body."""
    params = {"recipe_version_id": str(recipe_version_id)}
    if sample_row is not None:
        params["sample_row"] = json.dumps(sample_row)
    response = client.get(f"/api/v1/batches/{batch_id}/check", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --- adding the spreadsheet -------------------------------------------------------------------


def test_upload_a_csv_keeps_the_rows(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Two rows, the header as the columns, and every row stored ready with its key."""
    workflow_id, _ = add_task(conn, ledger.tenant_id)

    response = upload(
        connected,
        workflow_id,
        csv_bytes([["Name", "Email"], ["Priya Nair", "Priya@BrightPath.io"], ["Sam Lee", ""]]),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["row_count"] == 2
    assert body["columns"] == ["Name", "Email"]

    rows = stored_rows(conn, body["batch_id"])
    assert [row["row_index"] for row in rows] == [1, 2]
    assert rows[0]["raw"] == {"Name": "Priya Nair", "Email": "Priya@BrightPath.io"}
    assert rows[0]["clean"] == rows[0]["raw"]
    assert rows[0]["record_key"] == "priya@brightpath.io"
    # A row with no email falls back to its row number, exactly as the ledger's fixtures do.
    assert rows[1]["record_key"] == "row-0002"
    assert {row["status"] for row in rows} == {"ready"}
    assert [row["issues"] for row in rows] == [[], []]

    with conn.cursor() as cur:
        cur.execute("SELECT row_count, kind FROM input_batches WHERE id = %s", (body["batch_id"],))
        assert cur.fetchone() == (2, "csv")


def test_upload_an_xlsx_file(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The same shape from a workbook: the first sheet, its first row the header."""
    workflow_id, _ = add_task(conn, ledger.tenant_id)
    content = xlsx_bytes([["Email", "Interest"], ["a@x.com", "Data course"], ["b@x.com", "Other"]])

    response = upload(connected, workflow_id, content, filename="inquiries.xlsx")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["columns"] == ["Email", "Interest"]
    assert body["row_count"] == 2
    rows = stored_rows(conn, body["batch_id"])
    assert rows[0]["raw"] == {"Email": "a@x.com", "Interest": "Data course"}
    assert rows[0]["record_key"] == "a@x.com"


def test_upload_refuses_a_file_that_is_not_a_spreadsheet(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    workflow_id, _ = add_task(conn, ledger.tenant_id)

    response = upload(connected, workflow_id, b"Name,Email\nA,a@x.com\n", filename="rows.txt")

    assert response.status_code == 422, response.text
    assert ".csv" in response.json()["detail"]


def test_upload_refuses_a_file_with_no_rows(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    workflow_id, _ = add_task(conn, ledger.tenant_id)

    response = upload(connected, workflow_id, csv_bytes([["Name", "Email"]]))

    assert response.status_code == 422, response.text


def test_upload_refuses_more_rows_than_the_limit(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """2,001 rows is one more than a person adds at once, and nothing is written."""
    workflow_id, _ = add_task(conn, ledger.tenant_id)
    rows = [["Email"]] + [[f"row{index}@x.com"] for index in range(2001)]

    response = upload(connected, workflow_id, csv_bytes(rows))

    assert response.status_code == 422, response.text
    assert "2000" in response.json()["detail"]
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM input_batches WHERE workflow_id = %s", (workflow_id,))
        assert cur.fetchone()[0] == 0, "a refused upload writes no batch"


def test_upload_refuses_a_file_over_five_megabytes(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    workflow_id, _ = add_task(conn, ledger.tenant_id)

    response = upload(connected, workflow_id, b"Email\n" + b"a" * (5 * MEGABYTE))

    assert response.status_code == 413, response.text
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM input_batches WHERE workflow_id = %s", (workflow_id,))
        assert cur.fetchone()[0] == 0


def test_upload_of_an_unknown_task_is_not_found(connected: TestClient) -> None:
    """The tenant comes from the task, never from the caller, so an unknown task is a plain 404."""
    response = upload(connected, uuid.uuid4(), csv_bytes([["Email"], ["a@x.com"]]))

    assert response.status_code == 404, response.text


# --- the check --------------------------------------------------------------------------------


def test_the_check_matches_exact_synonym_token_overlap_and_nothing(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """`E-mail` is close, `Name` shares a token with `contact_name`, and `Notes` is none."""
    # `Contact name` is deliberately not used here: it normalises to exactly `contact_name`, so it
    # would be an exact match and would leave the token-overlap rule untested.
    document = recipe_document(
        fields={
            "email": {"type": "string", "required": True},
            "phone": {"type": "string"},
            "company": {"type": "string"},
            "contact_name": {"type": "string"},
            "reference": {"type": "string"},
        }
    )
    workflow_id, version_id = add_task(conn, ledger.tenant_id, document=document)
    content = csv_bytes(
        [
            ["Email", "E-mail", "Mobile", "Organisation", "Name", "Notes"],
            ["a@x.com", "a@x.com", "1", "Acme", "Sam", "hi"],
        ]
    )
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    body = check(connected, batch_id, version_id)

    found = {entry["column"]: entry for entry in body["mapping"]}
    # The check keeps one entry per task column; the recipe round-trips through jsonb, so the
    # set is the assertion, not the storage order.
    assert sorted(entry["column"] for entry in body["mapping"]) == sorted(
        document["meta"]["input_schema"]["fields"]
    )
    assert (found["email"]["chosen"], found["email"]["confidence"]) == ("Email", "exact")
    assert found["phone"]["chosen"] == "Mobile"
    assert found["phone"]["confidence"] == "close"
    assert found["company"]["chosen"] == "Organisation"
    assert found["company"]["confidence"] == "close"
    # `Name` and `contact_name` share no whole name, but they share a token (`name`), so this is
    # the token-overlap rule and not a synonym pair and not a normalised identical name.
    assert found["contact_name"]["chosen"] == "Name"
    assert found["contact_name"]["confidence"] == "close"
    assert found["reference"]["chosen"] is None
    assert found["reference"]["confidence"] == "none"
    assert found["email"]["label"] == "Email"
    assert found["contact_name"]["label"] == "Contact name"
    assert found["email"]["required"] is True
    # The alternatives are the other columns that matched, in file order.
    assert found["email"]["alternatives"] == ["E-mail"]


def test_the_check_matches_a_column_by_the_taught_sample_value(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Nothing in `Contact details` looks like `email`, but the sample value settles it."""
    document = recipe_document(fields={"email": {"type": "string", "required": True}})
    workflow_id, version_id = add_task(conn, ledger.tenant_id, document=document)
    content = csv_bytes([["Contact details"], ["priya.nair@brightpath.io"]])
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    body = check(connected, batch_id, version_id, sample_row={"email": "priya.nair@brightpath.io"})

    entry = body["mapping"][0]
    assert (entry["chosen"], entry["confidence"]) == ("Contact details", "exact")


def test_the_check_matches_a_column_by_the_example_stored_in_the_recipe(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The taught value lives on the recipe, so the check needs no ``sample_row`` passed.

    The web app never has the values the task was taught with, so the recipe carries them: the
    `Contact` column says nothing about `email`, and only the value in row 1 settles it (PL-028).
    """
    document = recipe_document(
        fields={"email": {"type": "string", "required": True, "example": "ana@x.test"}}
    )
    workflow_id, version_id = add_task(conn, ledger.tenant_id, document=document)
    content = csv_bytes([["Contact"], ["ana@x.test"]])
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    body = check(connected, batch_id, version_id)

    entry = body["mapping"][0]
    assert (entry["chosen"], entry["confidence"]) == ("Contact", "exact")
    assert body["rows_ok"] == 1


def test_the_check_lists_the_reasons_and_writes_the_verdicts(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A missing field, an answer the task does not know, and a repeat of an earlier email."""
    rules = [
        {"when": {"missing": "email"}, "then": "park", "reason": "No email to add the inquiry to."},
        {
            "when": {"not_in": {"field": "interest", "allowed_values": ["Data course"]}},
            "then": "park",
            "reason": "The inquiry is about something this task does not know.",
        },
    ]
    document = recipe_document(
        fields={"email": {"type": "string", "required": True}, "interest": {"type": "string"}},
        rules=rules,
        record_key="{{row.email | lower}}",
    )
    workflow_id, version_id = add_task(conn, ledger.tenant_id, document=document)
    content = csv_bytes(
        [
            ["Email", "Interest"],
            ["a@x.com", "Data course"],
            ["", "Data course"],
            ["c@x.com", "Nonsense"],
            ["A@x.com", "Data course"],
        ]
    )
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    body = check(connected, batch_id, version_id)

    assert body["recipe_name"] == "Add customer inquiries to Odoo CRM"
    assert body["columns"] == ["Email", "Interest"]
    assert body["row_count"] == 4
    assert body["rows_ok"] == 1
    assert body["rows_need_a_look"] == [
        {"row": 2, "reason": "No email"},
        {"row": 3, "reason": REASON_NOT_IN},
        {"row": 4, "reason": "Same email as row 1"},
    ]
    assert body["first_ready"] == {
        "row": 1,
        "values": {"email": "a@x.com", "interest": "Data course"},
    }

    rows = stored_rows(conn, batch_id)
    assert [row["status"] for row in rows] == [
        "ready",
        "needs_review",
        "needs_review",
        "needs_review",
    ]
    assert rows[1]["issues"] == ["No email"]
    assert rows[2]["issues"] == [REASON_NOT_IN]
    assert rows[3]["issues"] == ["Same email as row 1"]


def test_the_check_is_not_acknowledging_another_tenants_task(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A cross-tenant pin is not even acknowledged; a version of another task is a plain no."""
    workflow_id, _ = add_task(conn, ledger.tenant_id)
    other_tenant = conn.execute(
        "INSERT INTO tenants (name) VALUES ('Other tenant') RETURNING id"
    ).fetchone()[0]
    _, other_version = add_task(conn, other_tenant, name="Someone else's task")
    _, other_workflow_version = add_task(conn, ledger.tenant_id, name="Another task")
    batch_id = upload(connected, workflow_id, csv_bytes([["Email"], ["a@x.com"]])).json()[
        "batch_id"
    ]

    cross_tenant = connected.get(
        f"/api/v1/batches/{batch_id}/check", params={"recipe_version_id": str(other_version)}
    )
    wrong_task = connected.get(
        f"/api/v1/batches/{batch_id}/check",
        params={"recipe_version_id": str(other_workflow_version)},
    )

    assert cross_tenant.status_code == 404, cross_tenant.text
    assert wrong_task.status_code == 422, wrong_task.text


# --- the mapping, and the run it makes --------------------------------------------------------


def test_a_mapped_run_carries_the_tasks_own_column_names(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The person confirms `E-mail -> email`; the run's rows read `email`, the upload does not."""
    document = recipe_document(
        fields={"email": {"type": "string", "required": True}, "interest": {"type": "string"}}
    )
    workflow_id, version_id = add_task(conn, ledger.tenant_id, document=document)
    content = csv_bytes([["E-mail", "Interest"], ["a@x.com", "Data course"]])
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    saved = connected.put(
        f"/api/v1/batches/{batch_id}/mapping",
        json={"mapping": {"email": "E-mail", "interest": "Interest"}},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        "batch_id": batch_id,
        "mapping": {"email": "E-mail", "interest": "Interest"},
    }

    created = connected.post(
        "/api/v1/runs",
        json={
            "workflow_id": str(workflow_id),
            "recipe_version_id": str(version_id),
            "input_batch_id": batch_id,
            "mode": "batch",
        },
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]

    with conn.cursor() as cur:
        cur.execute("SELECT record_key FROM run_records WHERE run_id = %s", (run_id,))
        assert cur.fetchall() == [("a@x.com",)]
        cur.execute("SELECT clean FROM input_records WHERE batch_id = %s", (batch_id,))
        assert cur.fetchone()[0] == {"email": "a@x.com", "interest": "Data course"}
        cur.execute("SELECT raw FROM input_records WHERE batch_id = %s", (batch_id,))
        assert cur.fetchone()[0] == {"E-mail": "a@x.com", "Interest": "Data course"}


# --- the check honours the mapping a person saved -------------------------------------------------


def test_the_check_uses_the_mapping_a_person_saved(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A column the matcher cannot find: the person picks it, and the rows then become ready.

    The check writes each row's verdict, and a run is built only from the rows it marked ready, so
    a check that ignored the saved mapping would leave a person's own fix out of the run with no
    way to get it in. `Contact` holds the email and nothing about that name says `email`, so the
    first check refuses every row and only the confirmed mapping can make them ready.
    """
    document = recipe_document(
        fields={"email": {"type": "string", "required": True}},
        rules=[{"when": {"missing": "email"}, "then": "park", "reason": "no email"}],
    )
    workflow_id, version_id = add_task(conn, ledger.tenant_id, document=document)
    content = csv_bytes([["Contact", "Note"], ["a@x.com", "first"], ["b@x.com", "second"]])
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    first = check(connected, batch_id, version_id)
    assert first["mapping"][0]["chosen"] is None
    assert first["mapping"][0]["confidence"] == "none"
    assert first["rows_ok"] == 0
    assert [item["reason"] for item in first["rows_need_a_look"]] == ["No email", "No email"]
    assert first["first_ready"] is None

    saved = connected.put(
        f"/api/v1/batches/{batch_id}/mapping", json={"mapping": {"email": "Contact"}}
    )
    assert saved.status_code == 200, saved.text

    second = check(connected, batch_id, version_id)
    assert second["mapping"][0]["chosen"] == "Contact"
    # A column a person picked is the right one, whenever the matcher would have said.
    assert second["mapping"][0]["confidence"] == "exact"
    assert second["rows_ok"] == 2
    assert second["rows_need_a_look"] == []
    assert second["first_ready"] == {"row": 1, "values": {"email": "a@x.com"}}

    # And the run that follows is built from the rows the person's own mapping made ready.
    created = connected.post(
        "/api/v1/runs",
        json={
            "workflow_id": str(workflow_id),
            "recipe_version_id": str(version_id),
            "input_batch_id": batch_id,
            "mode": "batch",
        },
    )
    assert created.status_code == 201, created.text
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM run_records WHERE run_id = %s", (created.json()["run_id"],)
        )
        assert cur.fetchone()[0] == 2


def test_a_saved_mapping_that_names_no_column_is_ignored(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A saved name the file does not have is ignored, never trusted.

    The API refuses to save one in the first place; this is the second line of defence, for a row
    written round the API.
    """
    document = recipe_document(fields={"email": {"type": "string", "required": True}})
    workflow_id, version_id = add_task(conn, ledger.tenant_id, document=document)
    content = csv_bytes([["Email"], ["a@x.com"]])
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    refused = connected.put(
        f"/api/v1/batches/{batch_id}/mapping", json={"mapping": {"email": "Gone"}}
    )
    assert refused.status_code == 422, refused.text

    # Written round the API, the check still will not key a row off a column that is not there.
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE input_batches SET column_mapping = %s WHERE id = %s",
            (json.dumps({"email": "Gone"}), batch_id),
        )
    body = check(connected, batch_id, version_id)
    assert body["mapping"][0]["chosen"] == "Email"
    assert body["rows_ok"] == 1


def test_a_preview_of_one_row_makes_exactly_one_record(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """`Try one row` is `mode: "preview"` with `limit: 1`: the first row that is ready."""
    document = recipe_document(fields={"email": {"type": "string", "required": True}})
    workflow_id, version_id = add_task(conn, ledger.tenant_id, document=document)
    content = csv_bytes([["Email"], ["one@x.com"], ["two@x.com"], ["three@x.com"]])
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]
    check(connected, batch_id, version_id)
    saved = connected.put(
        f"/api/v1/batches/{batch_id}/mapping", json={"mapping": {"email": "Email"}}
    )
    assert saved.status_code == 200, saved.text

    created = connected.post(
        "/api/v1/runs",
        json={
            "workflow_id": str(workflow_id),
            "recipe_version_id": str(version_id),
            "input_batch_id": batch_id,
            "mode": "preview",
            "limit": 1,
        },
    )

    assert created.status_code == 201, created.text
    with conn.cursor() as cur:
        cur.execute(
            "SELECT record_key FROM run_records WHERE run_id = %s", (created.json()["run_id"],)
        )
        assert cur.fetchall() == [("one@x.com",)]


def test_saving_a_mapping_refuses_a_column_that_is_not_there(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Every problem is listed, and nothing is written."""
    workflow_id, _ = add_task(conn, ledger.tenant_id)
    batch_id = upload(connected, workflow_id, csv_bytes([["Email"], ["a@x.com"]])).json()[
        "batch_id"
    ]

    response = connected.put(
        f"/api/v1/batches/{batch_id}/mapping",
        json={"mapping": {"email": "Nowhere", "nickname": "Email"}},
    )

    assert response.status_code == 422, response.text
    problems = response.json()["detail"]["problems"]
    assert len(problems) == 2
    with conn.cursor() as cur:
        cur.execute("SELECT column_mapping FROM input_batches WHERE id = %s", (batch_id,))
        assert cur.fetchone()[0] is None
