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
    """Record a decision on an approval request and resume the run it belongs to."""
    with connection() as conn:
        request_row = get_request(conn, payload.request_id)
        if request_row is None:
            raise HTTPException(
                status_code=404, detail=f"approval request {payload.request_id} does not exist"
            )
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
