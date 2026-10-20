"""Turn a Word document into a clean CSV a person can download or use for a task.

The preview route takes the file as base64 inside JSON, not as a multipart form: the front door
(``middleware``) accepts a form body only on ``POST /api/v1/batches``, and this ticket may not
change that module. A downloaded CSV is stored in memory for an hour, keyed by an unguessable
token; the token is never logged and the document's contents never leave this process.
"""

from __future__ import annotations

import base64
import binascii
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from packages.documents import DocumentRefused, clean, csv_bytes, read_docx

router = APIRouter(prefix="/api/v1", tags=["documents"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
# A 5 MB file is about 6.7 MB once base64-encoded; anything longer cannot decode to 5 MB.
MAX_BASE64_CHARS = ((MAX_UPLOAD_BYTES + 2) // 3) * 4 + 4
TOKEN_TTL_SECONDS = 60 * 60
MAX_PREVIEW_ROWS = 50
DOCX_SUFFIX = ".docx"


class DocumentUpload(BaseModel):
    """A Word document travelling as JSON: its name, and its bytes in base64."""

    file_name: str
    content_base64: str


class DocumentPreview(BaseModel):
    """What the page shows after reading the document, and the token for the full CSV."""

    columns: list[str]
    rows: list[dict[str, str]]
    notes: list[str]
    csv_token: str


@dataclass(frozen=True)
class _StoredCsv:
    expires_at: float
    content: bytes


_store: dict[str, _StoredCsv] = {}
_lock = threading.Lock()


def _remember(content: bytes) -> str:
    """Keep the full CSV for an hour and return the token that fetches it."""
    token = secrets.token_urlsafe(24)
    with _lock:
        _store[token] = _StoredCsv(
            expires_at=time.monotonic() + TOKEN_TTL_SECONDS,
            content=content,
        )
    return token


def _forget_expired(now: float) -> None:
    with _lock:
        expired = [token for token, stored in _store.items() if stored.expires_at <= now]
        for token in expired:
            del _store[token]


def _lookup(token: str) -> bytes | None:
    now = time.monotonic()
    _forget_expired(now)
    with _lock:
        stored = _store.get(token)
        if stored is None or stored.expires_at <= now:
            return None
        return stored.content


def _content(payload: DocumentUpload) -> bytes:
    """The document's bytes, after the suffix and both size checks."""
    suffix = Path(payload.file_name.strip()).suffix.lower()
    if suffix != DOCX_SUFFIX:
        raise HTTPException(status_code=422, detail="Only a .docx file can be added here.")
    if len(payload.content_base64) > MAX_BASE64_CHARS:
        raise HTTPException(
            status_code=413,
            detail="This file is larger than 5 MB; split it and add it in parts.",
        )
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="This file is not a Word document.") from exc
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="This file is larger than 5 MB; split it and add it in parts.",
        )
    return content


def read_clean(
    content: bytes,
) -> tuple[list[str], list[dict[str, str]], list[str]]:
    """The reader and the cleaner, with a refusal turned into the API's own 422."""
    try:
        columns, raw = read_docx(content)
    except DocumentRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return clean(columns, [[row.get(column, "") for column in columns] for row in raw])


@router.post("/documents/to-csv", response_model=DocumentPreview)
def to_csv(payload: DocumentUpload) -> DocumentPreview:
    """Read a .docx and answer its columns, its first 50 rows, the notes, and a download token."""
    columns, rows, notes = read_clean(_content(payload))
    if not rows:
        raise HTTPException(status_code=422, detail="This file has no rows.")
    token = _remember(csv_bytes(columns, rows))
    return DocumentPreview(
        columns=columns,
        rows=rows[:MAX_PREVIEW_ROWS],
        notes=notes,
        csv_token=token,
    )


@router.get("/documents/to-csv/{token}.csv", response_class=Response)
def download(token: str) -> Response:
    """The full cleaned CSV, UTF-8 with a BOM, or a plain 404 once the token has expired."""
    content = _lookup(token)
    if content is None:
        raise HTTPException(status_code=404, detail="This download has expired.")
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="document.csv"'},
    )


__all__ = [
    "DocumentPreview",
    "DocumentUpload",
    "MAX_PREVIEW_ROWS",
    "MAX_UPLOAD_BYTES",
    "TOKEN_TTL_SECONDS",
    "download",
    "read_clean",
    "router",
    "to_csv",
]
