"""PL-106 item 3: a column whose values do not fit a field's kind is not auto-matched to it.

The pilot's sheet had a column "expected" holding "verified" and "skip". The task's field "Expected
revenue" was taught with a number, the two names share the word "expected", so the check guessed
that column, and then flagged every row as "isn't a number". A guess that makes most rows wrong is
not a guess worth making: the field is left "Not found" and the person picks, beside the dropdown.

A column the person picks is theirs (a saved mapping always wins); the rule is for the guess only.
The helpers are ``test_batches_api``'s.
"""

from __future__ import annotations

import psycopg
from fastapi.testclient import TestClient

from tests.api.test_batches_api import add_task, check, csv_bytes, recipe_document, upload
from tests.db.seed import Ledger


def _task(conn: psycopg.Connection, ledger: Ledger, fields: dict, key: str) -> tuple:
    document = recipe_document(fields=fields, record_key=key)
    return add_task(conn, ledger.tenant_id, document=document)


def _entry(body: dict, column: str) -> dict:
    return next(item for item in body["mapping"] if item["column"] == column)


def test_a_word_column_is_not_guessed_for_a_number_field(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    fields = {
        "name": {"type": "string", "example": "Acme deal"},
        "expected_revenue": {"type": "string", "example": "5000"},
    }
    workflow_id, version_id = _task(conn, ledger, fields, "{{row.name | lower}}")
    content = csv_bytes(
        [
            ["Name", "Expected"],
            ["Acme deal", "verified"],
            ["Bolt deal", "skip"],
            ["Cora deal", "verified"],
        ]
    )
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    body = check(connected, batch_id, version_id)

    entry = _entry(body, "expected_revenue")
    assert (entry["chosen"], entry["confidence"]) == (None, "none")
    # Nothing was guessed, so no row is flagged for a value it should never have read.
    assert body["rows_need_a_look"] == []
    assert body["rows_ok"] == 3


def test_a_word_column_is_not_guessed_for_an_email_field(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    fields = {
        "name": {"type": "string", "example": "Ana Ruiz"},
        "email": {"type": "string", "example": "ana@x.test"},
    }
    workflow_id, version_id = _task(conn, ledger, fields, "{{row.name | lower}}")
    content = csv_bytes(
        [
            ["Name", "Email opt-in"],
            ["Ana", "yes"],
            ["Gus", "no"],
            ["Bo", "yes"],
        ]
    )
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    body = check(connected, batch_id, version_id)

    assert _entry(body, "email")["chosen"] is None


def test_a_number_column_with_a_stray_word_is_still_guessed(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Most values fit, so the guess stands and the one stray value is flagged, as before."""
    fields = {
        "name": {"type": "string", "example": "Acme deal"},
        "expected_revenue": {"type": "string", "example": "5000"},
    }
    workflow_id, version_id = _task(conn, ledger, fields, "{{row.name | lower}}")
    content = csv_bytes(
        [
            ["Name", "Expected"],
            ["Acme deal", "1200"],
            ["Bolt deal", "free"],
            ["Cora deal", "$3,000"],
        ]
    )
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    body = check(connected, batch_id, version_id)

    assert _entry(body, "expected_revenue")["chosen"] == "Expected"
    assert body["rows_need_a_look"] == [{"row": 2, "reason": "“free” isn't a number"}]
