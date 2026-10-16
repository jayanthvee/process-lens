"""The approvals API.

A person decides an open approval request. The decision points at the exact request (and therefore
the exact values) it answered. An approved request lets the run continue; a rejected one parks the
record
with the reviewer's reason. Either way the run resumes so a worker can act on the decision (R3).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from packages.ledger import StateConflict, get_request, record_approval, resume_run

from .admin import require_active_person, runs_gate
from .db import connection

router = APIRouter(prefix="/api/v1", tags=["approvals"])


class ApprovalRequest(BaseModel):
    """A decision on one approval request."""

    request_id: uuid.UUID
    decision: Literal["approved", "rejected"]
    decided_by: uuid.UUID
    reason: str | None = None


class ApprovalResponse(BaseModel):
    approval_id: uuid.UUID
    request_id: uuid.UUID
    decision: str
    run_id: uuid.UUID
    resumed: bool


@router.post("/approvals", response_model=ApprovalResponse, status_code=201)
def record_approval_endpoint(payload: Annotated[ApprovalRequest, Body()]) -> ApprovalResponse:
    """Record a decision on an approval request and resume the run it belongs to.

    PL-026: a person whose access has been turned off cannot approve anything (403). Their runs keep
    the state they are in -- the decision is refused, nothing about the ledger is changed.

    Resuming the run is a resume, so it answers to the same gate `POST /runs/{id}/resume` does. A
    decision is still recorded while the workspace is frozen -- it is a person's answer about the
    values they were shown, and refusing to store it would lose it -- but the run is not restarted
    onto a frozen workspace, and `resumed` comes back false. Unfreezing does not resume it either:
    the person resumes the run, exactly as the ticket says.
    """
    with connection() as conn:
        require_active_person(conn, payload.decided_by)
        request_row = get_request(conn, payload.request_id)
        if request_row is None:
            raise HTTPException(
                status_code=404, detail=f"approval request {payload.request_id} does not exist"
            )
        runs_gate(conn, request_row.tenant_id)
        try:
            with conn.transaction():
                approval_id = record_approval(
                    conn,
                    request_id=payload.request_id,
                    decision=payload.decision,
                    decided_by=payload.decided_by,
                    reason=payload.reason,
                )
        except StateConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        resumed = resume_run(conn, request_row.run_id)
        conn.commit()

    return ApprovalResponse(
        approval_id=approval_id,
        request_id=payload.request_id,
        decision=payload.decision,
        run_id=request_row.run_id,
        resumed=resumed,
    )


__all__ = ["router"]
