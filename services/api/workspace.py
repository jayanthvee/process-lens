"""What the web app needs to know about the workspace it is working in.

The page shows two banners, and both are a fact about the workspace rather than about the screen the
person happens to be on:

* runs are paused by an admin, so nothing new will start;
* this person's Chrome extension is older than the floor their admin set.

Neither is a guard. The guard is the API's own gate on the run routes (``services/api/admin.py``),
which refuses with a 409 whatever the page happens to be showing; this route exists so the page
can say why before a person presses a button and gets refused. It is read-only, needs no admin
token, and shows nothing about anyone else.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from packages.ledger import UserRow, get_extension_report, get_tenant_admin_state, get_user

from .db import connection
from .operator import configured_operator_id, require_operator_user
from .versions import is_below

router = APIRouter(prefix="/api/v1", tags=["workspace"])

WORKSPACE_PATH = "/api/v1/workspace/state"

# The tenant the caller asked about has no operator to read an extension floor against. The two
# banners still answer -- the freeze is a fact about the tenant -- so this is not an error.
NO_OPERATOR_DETAIL = "there is no operator identity, so the workspace cannot be resolved"


class WorkspaceState(BaseModel):
    """One tenant's switches, and where the operator's own extension stands against the floor."""

    tenant_id: uuid.UUID
    runs_frozen: bool
    min_extension_version: str | None = None
    extension_version: str | None = None
    extension_out_of_date: bool = False


def _operator_for(conn: psycopg.Connection, tenant_id: uuid.UUID | None) -> UserRow | None:
    """The person this answer is about, when there is one.

    With an explicit ``tenant_id`` the caller already knows the company it is asking about, so a
    missing operator only means there is no extension version to compare. Without one the operator
    *is* the answer, and an id that names no person is refused.
    """
    if tenant_id is None:
        return require_operator_user(conn)
    person_id = configured_operator_id()
    return get_user(conn, person_id) if person_id is not None else None


@router.get("/workspace/state", response_model=WorkspaceState)
def workspace_state(
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
) -> WorkspaceState:
    """Whether runs are paused here, and whether this person's extension is too old.

    ``tenant_id`` is an optional filter for a caller that already knows the company it is asking
    about; without one the operator's own tenant is the answer. A tenant named but not there is a
    404, and having nobody to ask about is a 503 rather than a made-up workspace.
    """
    with connection() as conn:
        person = _operator_for(conn, tenant_id)
        target = tenant_id if tenant_id is not None else (person.tenant_id if person else None)
        if target is None:
            raise HTTPException(status_code=503, detail=NO_OPERATOR_DETAIL)
        state = get_tenant_admin_state(conn, target)
        if state is None:
            raise HTTPException(status_code=404, detail=f"tenant {target} does not exist")
        report = get_extension_report(conn, person.id) if person is not None else None

    reported = report.version if report is not None else None
    minimum = state.min_extension_version
    return WorkspaceState(
        tenant_id=state.tenant_id,
        runs_frozen=state.runs_frozen_at is not None,
        min_extension_version=minimum,
        extension_version=reported,
        extension_out_of_date=is_below(reported, minimum),
    )


__all__ = ["WORKSPACE_PATH", "WorkspaceState", "router"]
