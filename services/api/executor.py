"""The HTTP routes the Chrome extension calls outside the hub.

The hub carries step commands and nothing else; its frames and its protocol are unchanged (PL-026
requirement 3). The extension's one other need is to say which version it is running, so the API can
tell a person whose extension is too old, and that is a plain HTTP call on ``ready``.

One route, one table write: ``extension_reports`` holds the newest report per person. The caller is
the extension's service worker, so the route takes the same ``X-ProcessLens-Client`` header the
session route takes, and it is accepted from the extension's own origin by the front door
(``services/api/middleware.py``). The person it is stored against is the configured operator,
because that is the only identity this local build has.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Header, HTTPException
from pydantic import BaseModel

from packages.ledger import record_extension_report

from .db import connection
from .operator import require_operator_user
from .versions import InvalidVersion, parse_version

router = APIRouter(prefix="/api/v1", tags=["executor"])

EXTENSION_CLIENT = "extension"
VERSION_PATH = "/api/v1/executor/version"


class VersionReportRequest(BaseModel):
    """The version the extension's manifest carries."""

    version: str


class VersionReportResponse(BaseModel):
    """What the API stored, so the extension's own test can assert it landed."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    version: str
    reported_at: datetime


@router.post("/executor/version", response_model=VersionReportResponse, status_code=201)
def report_extension_version(
    payload: Annotated[VersionReportRequest, Body()],
    x_processlens_client: Annotated[str | None, Header()] = None,
) -> VersionReportResponse:
    """Store the version this person's extension just reported.

    A version the API cannot read is a 422: a report nobody can parse is refused rather than stored,
    because the extension floor is compared against it and a stored typo would read as "out of date"
    forever.
    """
    if x_processlens_client != EXTENSION_CLIENT:
        raise HTTPException(
            status_code=403, detail=f"X-ProcessLens-Client must be {EXTENSION_CLIENT}"
        )
    try:
        parse_version(payload.version)
    except InvalidVersion as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    with connection() as conn:
        person = require_operator_user(conn)
        with conn.transaction():
            report = record_extension_report(
                conn,
                user_id=person.id,
                tenant_id=person.tenant_id,
                version=payload.version.strip(),
            )
        conn.commit()

    return VersionReportResponse(
        user_id=report.user_id,
        tenant_id=report.tenant_id,
        version=report.version,
        reported_at=report.reported_at,
    )


__all__ = ["EXTENSION_CLIENT", "VERSION_PATH", "router"]
