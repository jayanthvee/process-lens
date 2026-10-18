"""The Spreadsheets page's own read: the batches a person uploaded, and how runs used them.

One row per ``csv``, ``xlsx`` or ``manual`` batch, newest first, with its task and the newest run
that used it. The read itself lives in ``packages.ledger.reads`` (the separation rule,
``tests/runner/test_sql_separation.py``); this module only shapes the JSON, so it writes no ledger
statement of its own and carries no row contents -- never ``input_records``, never ``raw``.

The page lists the upload, not the spreadsheet's values: the check's rows have their own screen.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from packages.ledger import SpreadsheetRow, list_spreadsheets

from .db import connection

router = APIRouter(prefix="/api/v1", tags=["spreadsheets"])


class SpreadsheetEntry(BaseModel):
    """One uploaded spreadsheet: its file, its task, and the newest run that used it."""

    id: uuid.UUID
    file_name: str
    task_id: uuid.UUID
    task_name: str
    row_count: int
    uploaded_at: datetime
    uploaded_by: str | None
    runs: int
    last_run_id: uuid.UUID | None


def _entry(row: SpreadsheetRow) -> SpreadsheetEntry:
    return SpreadsheetEntry(
        id=row.id,
        file_name=row.file_name,
        task_id=row.task_id,
        task_name=row.task_name,
        row_count=row.row_count,
        uploaded_at=row.uploaded_at,
        uploaded_by=row.uploaded_by,
        runs=row.runs,
        last_run_id=row.last_run_id,
    )


@router.get("/spreadsheets", response_model=list[SpreadsheetEntry])
def spreadsheets(tenant_id: Annotated[uuid.UUID | None, Query()] = None) -> list[SpreadsheetEntry]:
    """Every uploaded spreadsheet, newest first, with its task and the runs that used it."""
    with connection() as conn:
        rows = list_spreadsheets(conn, tenant_id=tenant_id)
    return [_entry(row) for row in rows]


__all__ = ["router"]
