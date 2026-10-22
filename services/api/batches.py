"""The batches API: add a spreadsheet, check it against a task, and save the mapping.

Everything here goes through :mod:`packages.ledger.batches`, so no ``input_batches`` or
``input_records`` statement lives in a service module. The tenant is taken from the task itself,
never from the caller, and a cross-tenant pin is not even acknowledged.

The three routes are the three steps of "Run this task":

1. ``POST /api/v1/batches`` reads a ``.csv``, ``.tsv``, ``.xlsx`` or ``.docx`` file into a batch
   and its rows (a Word document is read and cleaned first);
2. ``GET /api/v1/batches/{id}/check`` matches the spreadsheet's columns to the task's own columns,
   decides which rows are ready, and writes that verdict onto the rows (``create_run`` builds
   ledger rows only for ``status = 'ready'`` rows, so the verdict has to land before a run can
   exist);
3. ``PUT /api/v1/batches/{id}/mapping`` saves the mapping a person confirmed. The mapping lives on
   the batch; the task's recipe is never mutated.

Column matching is deterministic -- no model is called anywhere in this module. Exact names come
first, then a value match against the sample row the task was taught with, then token overlap and a
small synonym table. Normalising keeps digits, so ``Phone 1`` and ``Phone 2`` never collapse.
"""

from __future__ import annotations

import codecs
import csv
import io
import json
import re
import uuid
from collections.abc import Callable
from datetime import date, datetime, time
from pathlib import Path
from typing import Annotated, Any, Literal

import openpyxl
import psycopg
from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from packages.documents import DocumentRefused, clean, read_docx
from packages.ledger import (
    BatchRow,
    get_batch,
    insert_batch,
    list_batch_rows,
    set_batch_mapping,
    set_row_verdict,
)
from services.runner.templates import TemplateError, render

from .db import connection

router = APIRouter(prefix="/api/v1", tags=["batches"])

# A person's spreadsheet, not an import job: 5 MB and 2,000 rows are what the upload takes.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ROWS = 2000
# The alternatives shown beside a chosen column are a hint, not a list to read.
MAX_ALTERNATIVES = 5

CSV_SUFFIX = ".csv"
TSV_SUFFIX = ".tsv"
XLSX_SUFFIX = ".xlsx"
DOCX_SUFFIX = ".docx"
XLS_SUFFIX = ".xls"

_SELECT_WORKFLOW = """
SELECT id, tenant_id, archived_at FROM workflows WHERE id = %(id)s
"""
_SELECT_RECIPE_VERSION = """
SELECT id, tenant_id, workflow_id, recipe FROM recipe_versions WHERE id = %(id)s
"""
_SELECT_LATEST_RECIPE = """
SELECT id, tenant_id, workflow_id, recipe FROM recipe_versions
WHERE workflow_id = %(workflow)s
ORDER BY version DESC
LIMIT 1
"""

# The synonym groups the ticket freezes: the words a person actually puts on a spreadsheet header.
SYNONYMS: dict[str, frozenset[str]] = {
    "email": frozenset({"email", "e_mail", "mail"}),
    "phone": frozenset({"phone", "mobile", "tel"}),
    "company": frozenset({"company", "organisation", "organization", "account"}),
    "name": frozenset({"name", "full_name", "contact"}),
}

_ROW_TEMPLATE = re.compile(r"\{\{\s*row\.(?P<column>[A-Za-z0-9_]+)")


class BatchUploadResponse(BaseModel):
    """What the upload knows: which batch, how many rows, and the spreadsheet's columns."""

    batch_id: uuid.UUID
    row_count: int
    columns: list[str]
    # What the cleaner changed in a Word document, in the sentences a person reads.
    notes: list[str] = Field(default_factory=list)


class ColumnMapping(BaseModel):
    """One task column: what it matched, how sure the match is, and what else looked close."""

    column: str
    label: str
    chosen: str | None
    confidence: Literal["exact", "close", "none"]
    required: bool
    alternatives: list[str]


class NeedALook(BaseModel):
    """One row that is not ready, with one plain reason."""

    row: int
    reason: str


class FirstReady(BaseModel):
    """The first ready row, as the task's own columns see it."""

    row: int
    values: dict[str, Any]


class CheckResponse(BaseModel):
    """`GET /api/v1/batches/{id}/check`: the mapping, the row counts, and the rows to look at."""

    batch_id: uuid.UUID
    row_count: int
    columns: list[str]
    recipe_name: str
    mapping: list[ColumnMapping]
    rows_ok: int
    rows_need_a_look: list[NeedALook]
    first_ready: FirstReady | None


class MappingRequest(BaseModel):
    """The mapping a person confirmed: one entry per task column, ``None`` for "not in my file"."""

    mapping: dict[str, str | None]


class MappingResponse(BaseModel):
    """The mapping as it is now stored on the batch."""

    batch_id: uuid.UUID
    mapping: dict[str, str | None]


# --- reading the file -------------------------------------------------------------------------


def _header_text(value: Any) -> str:
    """A header's text, stripped; a blank header is named by its position later.

    A NUL is valid UTF-8 but not valid text: PostgreSQL refuses it in a ``text`` or ``jsonb``
    column, so a binary file renamed ``.csv`` would be a 500 rather than an upload.
    """
    text = "" if value is None else str(value)
    return text.replace("\x00", "").strip()


def _headers(names: list[Any]) -> list[str]:
    """Headers stripped, named by position when blank, and made unique.

    A generated ``Column N`` steps past every real header's name, so it never takes one; a
    repeated name gets " 2", " 3", the same rule ``packages.documents.clean`` uses.
    """
    real = {_header_text(name) for name in names if _header_text(name)}
    used: set[str] = set()
    result: list[str] = []
    for position, name in enumerate(names, start=1):
        base = _header_text(name)
        if not base:
            number = position
            base = f"Column {number}"
            while base in real or base in used:
                number += 1
                base = f"Column {number}"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base} {suffix}"
            suffix += 1
        used.add(candidate)
        result.append(candidate)
    return result


def _is_blank(cells: list[Any]) -> bool:
    """A row whose cells are all empty or whitespace, which no spreadsheet keeps."""
    return not any(value is not None and str(value).strip() for value in cells)


def _is_title(cells: list[Any]) -> bool:
    """A title row: at most one non-blank cell, and it is the row's first cell.

    A title is written in the sheet's first cell, so Excel pads the cells beside a merged title
    with empty ones and a saved CSV carries them as delimiters. A row whose first cell is blank
    but which names a column later is a header whose first column the person left unnamed, not a
    title: reading it as one would promote the first data row into the header.
    """
    filled = [
        index for index, value in enumerate(cells) if value is not None and str(value).strip()
    ]
    return len(filled) <= 1 and (not filled or filled[0] == 0)


def _split_header(rows: list[list[Any]]) -> tuple[list[Any], list[list[Any]]]:
    """The header row and the data below it, skipping the title rows above the header.

    The header is the first row that is not a title. When every row is title-shaped (a one-column
    file), the first row is the header, as before.
    """
    for index, cells in enumerate(rows):
        if not _is_title(cells):
            return cells, rows[index + 1 :]
    # Every row is title-shaped: a one-column file, where the first row is the header.
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _raw_text(value: Any) -> str:
    """A cell as ``str()`` has always written it: what the file held, minus any NUL."""
    return "" if value is None else str(value).replace("\x00", "")


def _clean_text(value: Any) -> str:
    """A cell a run can type: the same text, trimmed."""
    return _raw_text(value).strip()


def _excel_text(value: Any) -> str:
    """An Excel cell as a person reads it, not as ``str()`` prints its Python type."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        if value.time() == time(0, 0):
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _raw_text(value)


def _row_from(cells: list[Any], headers: list[str]) -> dict[str, str]:
    """One row as a dict: every value a string, a missing cell an empty string."""
    values = [_raw_text(cell) for cell in cells]
    values += [""] * max(0, len(headers) - len(values))
    return dict(zip(headers, values, strict=False))


def _clean_row(
    cells: list[Any], headers: list[str], format_cell: Callable[[Any], str]
) -> dict[str, str]:
    """The row a run types: every value trimmed, and Excel's own values made readable."""
    values = [format_cell(cell).strip() for cell in cells]
    values += [""] * max(0, len(headers) - len(values))
    return dict(zip(headers, values, strict=False))


def _decode_text(content: bytes) -> str:
    """The file's text, whatever encoding Excel used; a file is never refused for it.

    Excel's "Unicode Text" carries a UTF-16 BOM, and a plain CSV from Excel on Windows is written
    in the Windows code page, not in UTF-8. A truncated UTF-16 file has no text left to lose, so
    it decodes with replacement characters rather than raising.
    """
    if content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        # A truncated file leaves a replacement character where a character was cut in half;
        # it is not data, and keeping it would make a junk row.
        return content.decode("utf-16", errors="replace").replace("\ufffd", "")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return content.decode("cp1252")
    except UnicodeDecodeError:
        pass
    return content.decode("latin-1")


def _delimiter(text: str) -> str:
    """The separator a CSV uses, sniffed from its first 4 KB; comma when there is no answer."""
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _read_csv(
    content: bytes, suffix: str
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    """A comma- or tab-separated file: its encoding decoded, its header found, its cells kept."""
    text = _decode_text(content)
    delimiter = "\t" if suffix == TSV_SUFFIX else _delimiter(text)
    try:
        rows = [
            cells
            for cells in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
            if not _is_blank(cells)
        ]
    except csv.Error as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                "This file has a cell that is too long to read; split it into two files "
                "and add them one at a time."
            ),
        ) from exc
    if not rows:
        return [], [], []
    header_cells, body = _split_header(rows)
    headers = _headers(header_cells)
    return (
        headers,
        [_row_from(cells, headers) for cells in body],
        [_clean_row(cells, headers, _clean_text) for cells in body],
    )


def _read_xlsx(
    content: bytes,
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises several unrelated types
        raise HTTPException(status_code=422, detail="this .xlsx file could not be read") from exc
    try:
        sheet = workbook.worksheets[0]
        rows: list[list[Any]] = []
        for cells in sheet.iter_rows(values_only=True):
            row = list(cells)
            if not _is_blank(row):
                rows.append(row)
        if not rows:
            return [], [], []
        header_cells, body = _split_header(rows)
        headers = _headers(header_cells)
        return (
            headers,
            [_row_from(cells, headers) for cells in body],
            [_clean_row(cells, headers, _excel_text) for cells in body],
        )
    finally:
        workbook.close()


def _read_rows(
    suffix: str, content: bytes
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    if suffix in {CSV_SUFFIX, TSV_SUFFIX}:
        return _read_csv(content, suffix)
    return _read_xlsx(content)


def _record_key(row: dict[str, str], index: int) -> str:
    """The key built the way the ledger's own fixtures build it: the email, else the row number."""
    for name, value in row.items():
        if name.strip().lower() == "email":
            text = str(value).strip().lower()
            if text:
                return text
    return f"row-{index:04d}"


# --- matching ---------------------------------------------------------------------------------


def _normalise(text: Any) -> str:
    """Lowercase, strip, and collapse every run of non-letters and non-digits to one ``_``."""
    return re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")


def _group(normalised: str) -> str | None:
    """The synonym group a normalised word belongs to, if any."""
    for group, members in SYNONYMS.items():
        if normalised in members:
            return group
    return None


def _tokens(name: Any) -> set[str]:
    """The name's tokens, each replaced by its synonym group so ``contact`` meets ``name``."""
    return {_group(token) or token for token in _normalise(name).split("_") if token}


def _is_close(recipe_column: str, spreadsheet_column: str) -> bool:
    """A synonym pair, or two names that share a token once synonyms stand in for words."""
    left, right = _normalise(recipe_column), _normalise(spreadsheet_column)
    if not left or not right:
        return False
    group = _group(left)
    if group is not None and _group(right) == group:
        return True
    return bool(_tokens(recipe_column) & _tokens(spreadsheet_column))


def _rank(
    recipe_column: str,
    spreadsheet_column: str,
    sample_value: Any,
    row_one: dict[str, str],
) -> int:
    """0 exact name, 1 exact value, 2 close, 3 no match. Lower is better."""
    if _normalise(recipe_column) == _normalise(spreadsheet_column):
        return 0
    recorded = "" if sample_value is None else str(sample_value).strip()
    if recorded and str(row_one.get(spreadsheet_column, "")).strip() == recorded:
        return 1
    if _is_close(recipe_column, spreadsheet_column):
        return 2
    return 3


def _label(column: str) -> str:
    """A column's name in sentence case: ``contact_name`` -> ``Contact name``."""
    return " ".join(str(column).replace("_", " ").split()).capitalize()


def _match(
    recipe_column: str,
    columns: list[str],
    sample_value: Any,
    row_one: dict[str, str],
) -> tuple[str | None, str, list[str]]:
    """Choose a spreadsheet column for one task column, with a confidence and the alternatives."""
    ranks = [_rank(recipe_column, column, sample_value, row_one) for column in columns]
    best = min(ranks) if ranks else 3
    if best == 3:
        return None, "none", []
    chosen = columns[ranks.index(best)]
    confidence = "exact" if best <= 1 else "close"
    alternatives = [
        column
        for index, column in enumerate(columns)
        if ranks[index] != 3 and index != ranks.index(best)
    ]
    return chosen, confidence, alternatives[:MAX_ALTERNATIVES]


def _with_saved(entry: ColumnMapping, saved: dict[str, Any], columns: list[str]) -> ColumnMapping:
    """The mapping entry with the column a person saved for it, when there is one.

    A saved mapping is the person's own decision, so it wins over the guess. Its confidence is the
    guess's only while the two agree; a column a person picked is by definition the right one, and
    a value they set to nothing means "this column is not in my file". A saved name the file does
    not have (it cannot happen through this API, which validates it on the way in) is ignored
    rather than trusted, so a stale row can never key a row off a column that is not there.
    """
    if entry.column not in saved:
        return entry
    chosen = saved[entry.column]
    if chosen is not None and str(chosen) not in columns:
        return entry
    chosen = None if chosen is None else str(chosen)
    confidence = entry.confidence if chosen == entry.chosen else _chosen_confidence(chosen)
    return entry.model_copy(update={"chosen": chosen, "confidence": confidence})


def _chosen_confidence(chosen: str | None) -> Literal["exact", "close", "none"]:
    """A column a person picked is certain; one they cleared is not a match at all."""
    return "none" if chosen is None else "exact"


def _meta(document: dict[str, Any]) -> dict[str, Any]:
    meta = document.get("meta")
    return meta if isinstance(meta, dict) else {}


def _schema(document: dict[str, Any]) -> dict[str, Any]:
    """The task's own input schema, which the recipe keeps under ``meta``."""
    schema = _meta(document).get("input_schema")
    return schema if isinstance(schema, dict) else {}


def _fields(document: dict[str, Any]) -> dict[str, Any]:
    """The task's own columns, in the order the task wants them reviewed."""
    fields = _schema(document).get("fields")
    return fields if isinstance(fields, dict) else {}


def _mapping_for(
    document: dict[str, Any],
    columns: list[str],
    sample_row: dict[str, Any] | None,
    row_one: dict[str, str],
) -> list[ColumnMapping]:
    """One mapping entry per task column, in the task's own field order."""
    entries: list[ColumnMapping] = []
    for recipe_column, spec in _fields(document).items():
        sample_value = (sample_row or {}).get(recipe_column)
        chosen, confidence, alternatives = _match(recipe_column, columns, sample_value, row_one)
        required = bool(spec.get("required")) if isinstance(spec, dict) else False
        entries.append(
            ColumnMapping(
                column=recipe_column,
                label=_label(recipe_column),
                chosen=chosen,
                confidence=confidence,
                required=required,
                alternatives=alternatives,
            )
        )
    return entries


# --- the row verdict --------------------------------------------------------------------------


def _values_for(raw: dict[str, Any], mapping: dict[str, str | None]) -> dict[str, Any]:
    """The row as the task sees it: ``{task column: the spreadsheet's value}``."""
    values: dict[str, Any] = {}
    for recipe_column, spreadsheet_column in mapping.items():
        if not spreadsheet_column:
            continue
        value = raw.get(spreadsheet_column)
        values[recipe_column] = "" if value is None else value
    return values


def _rule_reasons(document: dict[str, Any], values: dict[str, Any]) -> list[str]:
    """Every precheck reason the task's own rules give this row, in rule order."""
    rules = _schema(document).get("rules")
    reasons: list[str] = []
    for rule in rules or []:
        when = rule.get("when") if isinstance(rule, dict) else None
        if not isinstance(when, dict):
            continue
        if "missing" in when:
            field = str(when["missing"])
            value = values.get(field)
            if not str(value or "").strip():
                reasons.append(f"No {_label(field).lower()}")
        elif "not_in" in when:
            spec = when["not_in"] if isinstance(when["not_in"], dict) else {}
            field = str(spec.get("field", ""))
            allowed = [str(item) for item in spec.get("allowed_values") or []]
            value = values.get(field)
            text = str(value or "").strip()
            if field and text and text not in allowed:
                reasons.append(f"\u201c{value}\u201d isn't one of the answers this task knows.")
    return reasons


def _duplicate_reason(
    template: str, values: dict[str, Any], seen: dict[str, int], row_index: int
) -> str | None:
    """The plain reason a row repeats an earlier row's key, or ``None`` when it does not."""
    if not template or "{{" not in template:
        return None
    try:
        key = render(template, row=values, variables={})
    except TemplateError:
        # The row cannot be keyed at all; creation refuses that run with its own message.
        return None
    if not key:
        return None
    if key in seen:
        match = _ROW_TEMPLATE.search(template)
        label = f" {_label(match.group('column')).lower()}" if match else ""
        return f"Same{label} as row {seen[key]}"
    seen[key] = row_index
    return None


def _row_reasons(
    document: dict[str, Any],
    values: dict[str, Any],
    template: str,
    seen: dict[str, int],
    row_index: int,
) -> list[str]:
    reasons = _rule_reasons(document, values)
    duplicate = _duplicate_reason(template, values, seen, row_index)
    if duplicate is not None:
        reasons.append(duplicate)
    return reasons


# --- lookups ----------------------------------------------------------------------------------


def _workflow(conn: psycopg.Connection, workflow_id: uuid.UUID) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_WORKFLOW, {"id": workflow_id})
        row = cur.fetchone()
    if row is None or row["archived_at"] is not None:
        raise HTTPException(status_code=404, detail=f"task {workflow_id} does not exist")
    return row


def _recipe_version(conn: psycopg.Connection, recipe_version_id: uuid.UUID) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_RECIPE_VERSION, {"id": recipe_version_id})
        row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"task version {recipe_version_id} does not exist"
        )
    return row


def _latest_recipe(conn: psycopg.Connection, workflow_id: uuid.UUID) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_LATEST_RECIPE, {"workflow": workflow_id})
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"task {workflow_id} has no task version yet")
    return row


def _document(row: dict[str, Any]) -> dict[str, Any]:
    recipe = row["recipe"]
    return recipe if isinstance(recipe, dict) else {}


def _columns_of(rows: list[Any]) -> list[str]:
    """The spreadsheet's columns in file order: the header row, as the first row stored it."""
    return list(rows[0].raw) if rows else []


# --- routes -----------------------------------------------------------------------------------


@router.post("/batches", response_model=BatchUploadResponse, status_code=201)
async def upload_batch(
    workflow_id: Annotated[uuid.UUID, Form(description="The task this spreadsheet is for.")],
    file: Annotated[UploadFile, File(description="A .csv, .tsv or .xlsx file.")],
) -> BatchUploadResponse:
    """Read a spreadsheet into a batch and its rows, all in one transaction.

    Fail closed: a wrong file type, a file over 5 MB, a file with no rows, and a file over 2,000
    rows are all refused before anything is written.
    """
    suffix = Path((file.filename or "").strip()).suffix.lower()
    if suffix == XLS_SUFFIX:
        raise HTTPException(
            status_code=422,
            detail=(
                "This is an old Excel file (.xls). In Excel, choose File > Save As > "
                "Excel Workbook (.xlsx), then add that file."
            ),
        )
    if suffix not in {CSV_SUFFIX, TSV_SUFFIX, XLSX_SUFFIX, DOCX_SUFFIX}:
        raise HTTPException(
            status_code=422,
            detail="Only a .csv, .tsv, .xlsx or .docx file can be added here.",
        )

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413, detail="This file is larger than 5 MB; split it and add it in parts."
        )

    notes: list[str] = []
    if suffix == DOCX_SUFFIX:
        try:
            columns, document_rows = read_docx(content)
        except DocumentRefused as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        columns, document_rows, notes = clean(
            columns, [[row.get(column, "") for column in columns] for row in document_rows]
        )
        raw_rows = document_rows
        clean_rows = [dict(row) for row in document_rows]
    else:
        columns, raw_rows, clean_rows = _read_rows(suffix, content)
    if not raw_rows:
        raise HTTPException(status_code=422, detail="This file has no rows.")
    if len(raw_rows) > MAX_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"This file has {len(raw_rows)} rows; at most {MAX_ROWS} can be added at once.",
        )

    with connection() as conn:
        workflow = _workflow(conn, workflow_id)
        with conn.transaction():
            batch_id = insert_batch(
                conn,
                tenant_id=workflow["tenant_id"],
                workflow_id=workflow_id,
                # A Word document is stored as the cleaned CSV it became, and a TSV as the CSV
                # it is: the rows are the cleaned rows, and no new batch kind is invented.
                kind="csv" if suffix in {DOCX_SUFFIX, TSV_SUFFIX} else suffix.lstrip("."),
                rows=[
                    BatchRow(
                        row_index=index,
                        raw=row,
                        clean=cleaned,
                        record_key=_record_key(row, index),
                    )
                    for index, (row, cleaned) in enumerate(
                        zip(raw_rows, clean_rows, strict=True), start=1
                    )
                ],
            )
        conn.commit()
    return BatchUploadResponse(
        batch_id=batch_id,
        row_count=len(raw_rows),
        columns=columns,
        notes=notes,
    )


@router.get("/batches/{batch_id}/check", response_model=CheckResponse)
def check_batch(
    batch_id: uuid.UUID,
    recipe_version_id: Annotated[
        uuid.UUID, Query(description="The task version to check against.")
    ],
    sample_row: Annotated[
        str | None,
        Query(
            description="The values typed while the task was taught, keyed by the task's column."
        ),
    ] = None,
) -> CheckResponse:
    """Match the spreadsheet's columns to the task's, and decide which rows are ready.

    The verdict is written onto the rows in the same request: a run is built from the rows whose
    ``status`` is ``ready``, so a check that only reported would leave the run impossible to make.
    """
    recorded: dict[str, Any] | None = None
    if sample_row:
        try:
            parsed = json.loads(sample_row)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="sample_row must be a JSON object") from exc
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=422, detail="sample_row must be a JSON object")
        recorded = parsed

    with connection() as conn:
        batch = get_batch(conn, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail=f"batch {batch_id} does not exist")
        version = _recipe_version(conn, recipe_version_id)
        # A cross-tenant pin is not even acknowledged, and a version of another task is a plain no.
        if version["tenant_id"] != batch.tenant_id:
            raise HTTPException(
                status_code=404, detail=f"task version {recipe_version_id} does not exist"
            )
        if version["workflow_id"] != batch.workflow_id:
            raise HTTPException(
                status_code=422,
                detail="this task version belongs to another task; check it against this task",
            )

        rows = list_batch_rows(conn, batch_id)
        columns = _columns_of(rows)
        document = _document(version)
        if recorded is None:
            # PL-028: the recipe carries the values the task was taught with, so the value match
            # works without the caller passing them again. A field with no example contributes
            # nothing, and a sample_row the caller passed always wins.
            recorded = {
                column: spec["example"]
                for column, spec in _fields(document).items()
                if isinstance(spec, dict)
                and isinstance(spec.get("example"), str)
                and spec["example"].strip()
            }
        row_one = rows[0].raw if rows else {}
        # The mapping a person saved is the one that counts: the check's own guess is only a
        # starting point, and the verdicts below -- which decide which rows a run is built from --
        # have to be computed from the columns they confirmed, not from the ones we guessed at.
        saved = batch.column_mapping or {}
        mapping = [
            _with_saved(entry, saved, columns)
            for entry in _mapping_for(document, columns, recorded, row_one)
        ]
        chosen = {entry.column: entry.chosen for entry in mapping}

        meta = _meta(document)
        template = str(meta.get("record_key", ""))

        verdicts: list[tuple[Any, list[str]]] = []
        seen: dict[str, int] = {}
        for row in rows:
            values = _values_for(row.raw, chosen)
            verdicts.append((row, _row_reasons(document, values, template, seen, row.row_index)))

        with conn.transaction():
            for row, reasons in verdicts:
                set_row_verdict(
                    conn,
                    row.id,
                    status="needs_review" if reasons else "ready",
                    issues=reasons,
                )
        conn.commit()

    need_a_look = [
        NeedALook(row=row.row_index, reason=reasons[0]) for row, reasons in verdicts if reasons
    ]
    first_ready: FirstReady | None = None
    for row, reasons in verdicts:
        if not reasons:
            first_ready = FirstReady(row=row.row_index, values=_values_for(row.raw, chosen))
            break

    return CheckResponse(
        batch_id=batch_id,
        row_count=len(rows),
        columns=columns,
        recipe_name=str(meta.get("name", "")),
        mapping=mapping,
        rows_ok=len(rows) - len(need_a_look),
        rows_need_a_look=need_a_look,
        first_ready=first_ready,
    )


@router.put("/batches/{batch_id}/mapping", response_model=MappingResponse)
def save_mapping(
    batch_id: uuid.UUID, payload: Annotated[MappingRequest, Body()]
) -> MappingResponse:
    """Save the mapping a person confirmed, on the batch, all in one transaction.

    Fail closed: a key that is not one of the task's columns, or a value that is not one of the
    spreadsheet's columns, is refused with every problem and nothing is written.
    """
    with connection() as conn:
        batch = get_batch(conn, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail=f"batch {batch_id} does not exist")
        version = _latest_recipe(conn, batch.workflow_id)
        columns = set(_columns_of(list_batch_rows(conn, batch_id)))
        fields = _fields(_document(version))

        problems: list[str] = []
        for column, chosen in payload.mapping.items():
            if column not in fields:
                problems.append(f"{column!r} is not a column of this task")
            elif chosen is not None and chosen not in columns:
                problems.append(f"{chosen!r} is not a column of your spreadsheet")
        if problems:
            raise HTTPException(
                status_code=422,
                detail={"message": "the mapping could not be saved", "problems": problems},
            )

        with conn.transaction():
            set_batch_mapping(conn, batch_id, dict(payload.mapping))
        conn.commit()

    return MappingResponse(batch_id=batch_id, mapping=payload.mapping)


__all__ = ["router"]
