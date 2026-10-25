"""PL-095: the check flags a value that cannot be what the task was taught with.

The pilot's messy files on Render: a price of "free" was saved as 0, "-5.00" was saved as a negative
price, and "gus.hale.at.example" was created as a lead's email. Each was verified. The task's own
example value is the only type information a recorded task has, so the check reads the kind from it.
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


def test_a_word_or_a_negative_in_a_number_column_is_flagged(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    fields = {
        "sku": {"type": "string", "example": "PL-CHR-2001"},
        "price": {"type": "string", "example": "349.00"},
    }
    workflow_id, version_id = _task(conn, ledger, fields, "{{row.sku | lower}}")
    content = csv_bytes(
        [
            ["SKU", "New Price"],
            ["PL-A-1", "192.00"],
            ["PL-A-2", "$92"],
            ["PL-A-3", '"1,232.00"'],
            ["PL-A-4", "free"],
            ["PL-A-5", "-5.00"],
            ["PL-A-6", "0"],
        ]
    )
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    body = check(connected, batch_id, version_id)

    assert body["rows_need_a_look"] == [
        {"row": 4, "reason": "“free” isn't a number"},
        {"row": 5, "reason": "“-5.00” is below zero"},
    ]
    assert body["rows_ok"] == 4


def test_a_value_that_is_not_an_email_in_an_email_column_is_flagged(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    fields = {
        "name": {"type": "string", "example": "Ana Ruiz"},
        "email": {"type": "string", "example": "ana@x.test"},
    }
    workflow_id, version_id = _task(conn, ledger, fields, "{{row.email | lower}}")
    content = csv_bytes(
        [
            ["Name", "Email"],
            ["Ana", "ana@x.test"],
            ["Gus", "gus.hale.at.example"],
            ["Bo", "bo@@x.test"],
        ]
    )
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    body = check(connected, batch_id, version_id)

    assert body["rows_need_a_look"] == [
        {"row": 2, "reason": "“gus.hale.at.example” isn't an email"},
        {"row": 3, "reason": "“bo@@x.test” isn't an email"},
    ]


def test_columns_taught_with_text_or_phones_are_not_judged(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A name, a phone with spaces, a long digit run (a phone or an ID) are not numbers to check."""
    fields = {
        "email": {"type": "string", "example": "ana@x.test"},
        "phone": {"type": "string", "example": "+1 555 0101"},
        "account": {"type": "string", "example": "5550101123"},
        "company": {"type": "string", "example": "Ruiz Hermanos"},
    }
    workflow_id, version_id = _task(conn, ledger, fields, "{{row.email | lower}}")
    content = csv_bytes(
        [
            ["Email", "Phone", "Account", "Company"],
            ["a@x.test", "(555) 010-2000 ext 4", "AC-77", "12"],
        ]
    )
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    body = check(connected, batch_id, version_id)

    assert body["rows_need_a_look"] == []
    assert body["rows_ok"] == 1


def test_an_empty_value_is_left_to_the_missing_rule(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Blank is not "not a number": only a column the person marked required flags a blank."""
    fields = {
        "email": {"type": "string", "example": "ana@x.test"},
        "price": {"type": "string", "example": "10"},
    }
    workflow_id, version_id = _task(conn, ledger, fields, "{{row.email | lower}}")
    content = csv_bytes([["Email", "Price"], ["a@x.test", ""]])
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    body = check(connected, batch_id, version_id)

    assert body["rows_need_a_look"] == []


def test_a_task_column_named_after_the_search_matches_its_column_by_shape(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The product task's key column is called "search" and the file says "SKU" (pilot run).

    Row one's SKU is another product, so it never equals the example, but it has the example's
    shape (letters-letters-digits), and no other column does.
    """
    fields = {
        "search": {"type": "string", "example": "PL-CHR-2001"},
        "sales_price": {"type": "string", "example": "349.00"},
    }
    workflow_id, version_id = _task(conn, ledger, fields, "{{row.search | lower}}")
    content = csv_bytes([["SKU", "New Price", "Note"], ["PL-KEY-5002", "192.00", "ok"]])
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]

    body = check(connected, batch_id, version_id)

    entry = next(item for item in body["mapping"] if item["column"] == "search")
    assert (entry["chosen"], entry["confidence"]) == ("SKU", "close")


def test_a_repeated_key_is_named_after_the_files_own_column(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """ "Same search as row 1" meant nothing to the person; their file calls it SKU."""
    fields = {
        "search": {"type": "string", "example": "PL-CHR-2001"},
        "sales_price": {"type": "string", "example": "349.00"},
    }
    workflow_id, version_id = _task(conn, ledger, fields, "{{row.search | lower}}")
    content = csv_bytes([["SKU", "New Price"], ["PL-A-1", "10"], ["pl-a-1", "11"]])
    batch_id = upload(connected, workflow_id, content).json()["batch_id"]
    connected.put(
        f"/api/v1/batches/{batch_id}/mapping",
        json={"mapping": {"search": "SKU", "sales_price": "New Price"}},
    )

    body = check(connected, batch_id, version_id)

    assert body["rows_need_a_look"] == [{"row": 2, "reason": "Same SKU as row 1"}]
