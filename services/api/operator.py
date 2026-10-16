"""The one local operator identity, read from the environment.

There are no users yet, so the operator id is configuration: ``PROCESSLENS_OPERATOR_ID`` names the
one person the local page and the gates act as. The reader lives here rather than in one route
because four callers now need the same answer -- the operator endpoint, the workspace state, the
admin endpoints, and the freeze and extension-floor gates -- and a second copy of it is how two
answers drift apart.

The API never invents an id: an unset, blank or malformed value is a 503 with a sentence the page
can show, and an id that names no row in ``users`` is a separate, later question.
"""

from __future__ import annotations

import os
import uuid

from fastapi import HTTPException
from psycopg import Connection

from packages.ledger import UserRow, get_user

OPERATOR_ENV_VAR = "PROCESSLENS_OPERATOR_ID"

_NOT_SET_DETAIL = f"{OPERATOR_ENV_VAR} is not set, so there is no operator id to act as"
_NOT_A_UUID_DETAIL = f"{OPERATOR_ENV_VAR} is not a UUID"
_NO_SUCH_USER_DETAIL = f"{OPERATOR_ENV_VAR} names no person in this database"


def operator_id() -> uuid.UUID:
    """The configured operator id. Raises 503 when it is unset, blank or not a UUID."""
    raw = os.environ.get(OPERATOR_ENV_VAR, "").strip()
    if not raw:
        raise HTTPException(status_code=503, detail=_NOT_SET_DETAIL)
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=_NOT_A_UUID_DETAIL) from exc


def configured_operator_id() -> uuid.UUID | None:
    """The configured operator id, or ``None`` when there is none.

    The gates use this instead of :func:`operator_id` where an unset id must not turn a request
    away on its own: there is nobody to hold to a rule, so the rule does not apply.
    """
    try:
        return operator_id()
    except HTTPException:
        return None


def operator_user(conn: Connection) -> UserRow | None:
    """The configured operator's ``users`` row, or ``None`` when there is no such person."""
    user_id = configured_operator_id()
    if user_id is None:
        return None
    return get_user(conn, user_id)


def require_operator_user(conn: Connection) -> UserRow:
    """The configured operator's row, refusing when the id names no person."""
    user_id = operator_id()
    user = get_user(conn, user_id)
    if user is None:
        raise HTTPException(status_code=403, detail=_NO_SUCH_USER_DETAIL)
    return user


__all__ = [
    "OPERATOR_ENV_VAR",
    "configured_operator_id",
    "operator_id",
    "operator_user",
    "require_operator_user",
]
