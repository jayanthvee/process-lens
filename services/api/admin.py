"""The admin API: the freeze switch, the extension floor, and the run gate.

The admin panel lives in its own repository (D-023) and reaches the database through a
least-privilege role that cannot write the ledger, so everything that affects runs has to come back
here, to the ProcessLens API (D-024). This module is that surface, and only that surface: three
endpoints that change state, each writing its ``admin_audit`` row in the same transaction as the
change it describes, plus the gate the run routes call.

Until sign-in lands with the Railway ticket, the callers are trusted by a shared token in a header
whose value comes from ``PROCESSLENS_ADMIN_TOKEN`` -- read from the environment, never committed --
together with the front door's own rule that every route answers only on the local interface. An
unset token closes the admin surface to everyone rather than opening it to everyone, exactly as an
unset runner secret closes the runner channel.

:func:`runs_gate` is the product rule both this ticket and the panel depend on:

* a tenant whose runs are frozen refuses to start or resume a run, with one sentence;
* a tenant with an extension floor refuses when the caller's reported version is below it, with the
  version named in the sentence;
* a person who has been turned off cannot start, resume or approve anything.

Runs already going are not touched by any of it: the freeze pauses a run at its next step boundary
through the runner, and the floor never interrupts a run at all.
"""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from psycopg import Connection
from pydantic import BaseModel

from packages.ledger import (
    freeze_runs,
    get_extension_report,
    get_tenant_admin_state,
    get_user,
    record_admin_action,
    set_min_extension_version,
    unfreeze_runs,
)

from .db import connection
from .operator import operator_user, require_operator_user
from .versions import InvalidVersion, is_below, parse_version

router = APIRouter(prefix="/api/v1", tags=["admin"])

ADMIN_TOKEN_ENV_VAR = "PROCESSLENS_ADMIN_TOKEN"
# The header the admin panel sends. The environment variable's own name is accepted as a second
# spelling, so a caller that reads the ticket literally still gets in.
ADMIN_TOKEN_HEADERS = ("x-processlens-admin-token", ADMIN_TOKEN_ENV_VAR.lower())

# The sentence the product uses for a frozen tenant, and the one it uses for an old extension.
RUNS_FROZEN_DETAIL = "Runs are paused by an admin"
EXTENSION_FLOOR_TEMPLATE = "Update the ProcessLens Chrome extension to version {version} or newer"

ADMIN_MODULE = "core"

TOKEN_NOT_SET_DETAIL = f"{ADMIN_TOKEN_ENV_VAR} is not set, so the admin API is closed"
TOKEN_REFUSED_DETAIL = "the admin token is wrong"


class FreezeRequest(BaseModel):
    """Which company to pause, and who is doing it when the caller says."""

    tenant_id: uuid.UUID | None = None
    reason: str | None = None


class MinExtensionVersionRequest(BaseModel):
    """The new floor. ``null`` clears it, so any version may run again."""

    version: str | None = None
    tenant_id: uuid.UUID | None = None


class AdminState(BaseModel):
    """The tenant's admin switches, as the caller last left them."""

    tenant_id: uuid.UUID
    runs_frozen: bool
    runs_frozen_at: datetime | None = None
    runs_frozen_by: uuid.UUID | None = None
    min_extension_version: str | None = None


def require_admin_token(request: Request) -> None:
    """Refuse an admin request without the shared token (a dependency on every admin route)."""
    configured = os.environ.get(ADMIN_TOKEN_ENV_VAR, "").strip()
    if not configured:
        raise HTTPException(status_code=503, detail=TOKEN_NOT_SET_DETAIL)
    presented = ""
    for header in ADMIN_TOKEN_HEADERS:
        value = request.headers.get(header)
        if value:
            presented = value.strip()
            break
    if not presented or not presented.isascii():
        raise HTTPException(status_code=403, detail=TOKEN_REFUSED_DETAIL)
    if not secrets.compare_digest(presented, configured):
        raise HTTPException(status_code=403, detail=TOKEN_REFUSED_DETAIL)


def _tenant_for(conn: Connection, requested: uuid.UUID | None) -> uuid.UUID:
    """The tenant an admin action applies to: the one asked for, or the operator's own.

    An explicit ``tenant_id`` is how the panel acts for one company while one person's id is
    configured; without one the operator's own tenant is the honest default. A tenant that does not
    exist is a 404 rather than a silent no-op, and no tenant at all is a 403 naming the reason.
    """
    if requested is not None:
        if get_tenant_admin_state(conn, requested) is None:
            raise HTTPException(status_code=404, detail=f"tenant {requested} does not exist")
        return requested
    user = require_operator_user(conn)
    return user.tenant_id


def _state(conn: Connection, tenant_id: uuid.UUID) -> AdminState:
    """Read the switches back after a change, so the caller sees what it just did."""
    state = get_tenant_admin_state(conn, tenant_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"tenant {tenant_id} does not exist")
    return AdminState(
        tenant_id=state.tenant_id,
        runs_frozen=state.runs_frozen_at is not None,
        runs_frozen_at=state.runs_frozen_at,
        runs_frozen_by=state.runs_frozen_by,
        min_extension_version=state.min_extension_version,
    )


@router.post(
    "/admin/freeze", response_model=AdminState, dependencies=[Depends(require_admin_token)]
)
def freeze_endpoint(
    payload: Annotated[FreezeRequest, Body()] | None = None,
) -> AdminState:
    """Pause everything for one tenant, and record who did it.

    The change and its ``admin_audit`` row commit together: an action without its audit row must not
    survive. Unfreezing does not resume anything -- each run is resumed by a person.
    """
    body = payload or FreezeRequest()
    with connection() as conn:
        tenant_id = _tenant_for(conn, body.tenant_id)
        actor = operator_user(conn)
        with conn.transaction():
            if not freeze_runs(conn, tenant_id, actor.id if actor else None):
                raise HTTPException(status_code=404, detail=f"tenant {tenant_id} does not exist")
            record_admin_action(
                conn,
                tenant_id=tenant_id,
                action="freeze_runs",
                module=ADMIN_MODULE,
                actor_user_id=actor.id if actor else None,
                target=str(tenant_id),
                detail={"reason": body.reason} if body.reason else None,
            )
        conn.commit()
        return _state(conn, tenant_id)


@router.post(
    "/admin/unfreeze", response_model=AdminState, dependencies=[Depends(require_admin_token)]
)
def unfreeze_endpoint(
    payload: Annotated[FreezeRequest, Body()] | None = None,
) -> AdminState:
    """Let runs start again for one tenant. Nothing that is paused is resumed by this."""
    body = payload or FreezeRequest()
    with connection() as conn:
        tenant_id = _tenant_for(conn, body.tenant_id)
        actor = operator_user(conn)
        with conn.transaction():
            if not unfreeze_runs(conn, tenant_id, actor.id if actor else None):
                raise HTTPException(status_code=404, detail=f"tenant {tenant_id} does not exist")
            record_admin_action(
                conn,
                tenant_id=tenant_id,
                action="unfreeze_runs",
                module=ADMIN_MODULE,
                actor_user_id=actor.id if actor else None,
                target=str(tenant_id),
                detail={"reason": body.reason} if body.reason else None,
            )
        conn.commit()
        return _state(conn, tenant_id)


@router.post(
    "/admin/min-extension-version",
    response_model=AdminState,
    dependencies=[Depends(require_admin_token)],
)
def min_extension_version_endpoint(
    payload: Annotated[MinExtensionVersionRequest, Body()] | None = None,
) -> AdminState:
    """Set or clear the oldest Chrome extension version this tenant may run on.

    A version the API cannot read is refused with a 422, so a malformed floor is never stored; a
    run that is already going is never interrupted by a new one.
    """
    body = payload or MinExtensionVersionRequest()
    version: str | None = None
    if body.version is not None:
        try:
            parse_version(body.version)
        except InvalidVersion as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        version = body.version.strip()
    with connection() as conn:
        tenant_id = _tenant_for(conn, body.tenant_id)
        actor = operator_user(conn)
        with conn.transaction():
            if not set_min_extension_version(conn, tenant_id, version):
                raise HTTPException(status_code=404, detail=f"tenant {tenant_id} does not exist")
            record_admin_action(
                conn,
                tenant_id=tenant_id,
                action="set_min_extension_version",
                module=ADMIN_MODULE,
                actor_user_id=actor.id if actor else None,
                target=str(tenant_id),
                detail={"version": version},
            )
        conn.commit()
        return _state(conn, tenant_id)


# ---------------------------------------------------------------------------
# The gate the run routes call
# ---------------------------------------------------------------------------


def require_active_person(conn: Connection, user_id: uuid.UUID) -> None:
    """Refuse a person whose access has been turned off (403).

    Their open runs keep their state -- this refuses what they may start or decide, and nothing
    about the runs already in the ledger.
    """
    user = get_user(conn, user_id)
    if user is not None and user.deactivated_at is not None:
        raise HTTPException(
            status_code=403,
            detail="this person's access has been turned off, so they cannot act on runs",
        )


def require_active_operator(conn: Connection) -> None:
    """Refuse the configured operator when their access has been turned off (403)."""
    person = operator_user(conn)
    if person is not None and person.deactivated_at is not None:
        require_active_person(conn, person.id)


def extension_floor_detail(conn: Connection, minimum: str) -> str | None:
    """The refusal sentence when the caller's reported version is below the floor, else ``None``.

    Nobody to check means no refusal: with no operator configured there is no reported version, and
    a person who has never reported one is not below a floor either -- "not seen yet" is not "out of
    date", which is also what the page's banner says.
    """
    person = operator_user(conn)
    if person is None:
        return None
    report = get_extension_report(conn, person.id)
    if not is_below(report.version if report else None, minimum):
        return None
    return EXTENSION_FLOOR_TEMPLATE.format(version=minimum)


def runs_gate(conn: Connection, tenant_id: uuid.UUID) -> None:
    """Refuse to start or resume a run in this tenant, with the product's own sentence.

    The freeze is asked first, so a frozen tenant answers with the freeze even when its extension
    floor would also apply. Raises 409 in either case.
    """
    state = get_tenant_admin_state(conn, tenant_id)
    if state is None:
        return
    if state.runs_frozen_at is not None:
        raise HTTPException(status_code=409, detail=RUNS_FROZEN_DETAIL)
    if state.min_extension_version is None:
        return
    detail = extension_floor_detail(conn, state.min_extension_version)
    if detail is not None:
        raise HTTPException(status_code=409, detail=detail)


def require_runs_allowed(conn: Connection, tenant_id: uuid.UUID) -> None:
    """The two rules a run-starting route applies, in order: who you are, then whether runs run."""
    require_active_operator(conn)
    runs_gate(conn, tenant_id)


__all__ = [
    "ADMIN_MODULE",
    "ADMIN_TOKEN_ENV_VAR",
    "EXTENSION_FLOOR_TEMPLATE",
    "RUNS_FROZEN_DETAIL",
    "extension_floor_detail",
    "require_active_operator",
    "require_active_person",
    "require_admin_token",
    "require_runs_allowed",
    "router",
    "runs_gate",
]
