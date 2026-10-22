"""Hosted mode: who may use an API that is not on this machine.

On a laptop the API answers only the local interface, and that is its whole access rule
(``middleware``, ``hub``). Hosted (PL-086), the caller is never local: every request arrives
through the platform's proxy. So two settings switch the rule:

* ``PROCESSLENS_PUBLIC_URL`` is the API's own address, ``https://processlens-api.onrender.com``.
  On Render it may stay unset: Render gives every web service ``RENDER_EXTERNAL_URL``, which is
  read when this is not. Either one set, the API is hosted: the ``Host`` header must name that
  address (still the DNS-rebinding check), and its origin is this API's own for the CSRF check.
* ``PROCESSLENS_ACCESS_CODE`` is the team's access code. Hosted, every request must carry one of
  three credentials instead of a loopback peer:

  - the web page's cookie (``pl_access``), set by ``POST /api/v1/access`` once a person types the
    code. It holds an HMAC of the code, never the code, and is HttpOnly, SameSite=Strict;
  - ``Authorization: Bearer <access code>``, which the extension sends;
  - ``Authorization: Bearer <runner secret>``, which the supervisor and the runner send.

A hosted API with no access code refuses everything but its health check rather than opening to
the internet. A loopback peer is not a credential when hosted, so nothing that can spoof a
forwarded address can pass for local. Unset ``PROCESSLENS_PUBLIC_URL`` and nothing here applies:
local mode is exactly what it was.

Three things pass without a credential when hosted: ``GET /health`` (the platform's check, which
may use an internal ``Host``), the access routes themselves, and ``GET`` of the page's own files
outside ``/api/`` -- the page holds no data, and it asks for the code before it reads any.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.requests import HTTPConnection

router = APIRouter(prefix="/api/v1", tags=["access"])

PUBLIC_URL_KEY = "PROCESSLENS_PUBLIC_URL"
RENDER_URL_KEY = "RENDER_EXTERNAL_URL"
ACCESS_CODE_KEY = "PROCESSLENS_ACCESS_CODE"
RUNNER_SECRET_KEY = "PROCESSLENS_RUNNER_SECRET"

ACCESS_COOKIE = "pl_access"
ACCESS_PATH = "/api/v1/access"
HEALTH_PATH = "/health"
API_PREFIX = "/api/"
# A person signs in once a month at most on the same browser.
COOKIE_MAX_AGE_SECONDS = 30 * 24 * 3600
# What the cookie signs. Changing the access code changes every cookie, so a rotated code signs
# everyone out.
COOKIE_MESSAGE = b"processlens web access v1"

ACCESS_DETAIL = "Enter the team access code to use ProcessLens."
WRONG_CODE_DETAIL = "That access code is not right."


def public_origin() -> str | None:
    """``scheme://host[:port]`` of ``PROCESSLENS_PUBLIC_URL``, lowercased; ``None`` when local."""
    raw = os.environ.get(PUBLIC_URL_KEY, "").strip() or os.environ.get(RENDER_URL_KEY, "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def hosted() -> bool:
    """True when the API runs at a public address, so the local-only rule cannot apply."""
    return public_origin() is not None


def public_host() -> str | None:
    """The ``Host`` a hosted API answers to: the public address's ``host[:port]``."""
    origin = public_origin()
    return origin.split("://", 1)[1] if origin else None


def host_is_public(host_header: str | None) -> bool:
    """True when the ``Host`` header names the public address (a default port may be implied)."""
    expected = public_host()
    if expected is None or not host_header:
        return False
    given = host_header.strip().lower()
    if given == expected:
        return True
    origin = public_origin() or ""
    default_port = ":443" if origin.startswith("https://") else ":80"
    return given == f"{expected}{default_port}"


def _access_code() -> str:
    return os.environ.get(ACCESS_CODE_KEY, "").strip()


def _runner_secret() -> str:
    return os.environ.get(RUNNER_SECRET_KEY, "").strip()


def cookie_value(code: str) -> str:
    """The cookie a correct code earns: an HMAC of a fixed message, keyed by the code."""
    return hmac.new(code.encode("utf-8"), COOKIE_MESSAGE, hashlib.sha256).hexdigest()


def _same(given: str, expected: str) -> bool:
    if not expected or not given or not given.isascii():
        return False
    return secrets.compare_digest(given, expected)


def _bearer(connection: HTTPConnection) -> str:
    scheme, _, value = (connection.headers.get("authorization") or "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def has_credential(connection: HTTPConnection) -> bool:
    """True when the caller presents the cookie, the access code, or the runner secret."""
    code = _access_code()
    bearer = _bearer(connection)
    if code and _same(bearer, code):
        return True
    if _same(bearer, _runner_secret()):
        return True
    cookie = connection.cookies.get(ACCESS_COOKIE, "")
    return bool(code) and _same(cookie, cookie_value(code))


def open_without_credential(method: str, path: str) -> bool:
    """The three things a hosted API answers before anyone signs in (see the module text)."""
    if path == HEALTH_PATH and method in {"GET", "HEAD"}:
        return True
    if path.rstrip("/") == ACCESS_PATH:
        return True
    return method in {"GET", "HEAD"} and not path.startswith(API_PREFIX) and path != "/api"


def connection_is_allowed(connection: HTTPConnection) -> bool:
    """The hosted rule for one HTTP request or socket: the public ``Host`` and a credential."""
    return host_is_public(connection.headers.get("host")) and has_credential(connection)


# ---------------------------------------------------------------- the sign-in routes


class AccessState(BaseModel):
    """Whether this API asks for a code, and whether this browser already gave it."""

    required: bool
    granted: bool


class AccessRequest(BaseModel):
    code: str


@router.get(ACCESS_PATH.removeprefix("/api/v1"), response_model=AccessState)
def read_access(request: Request) -> AccessState:
    """What the page needs before it draws anything: is there a door, and is it open for us."""
    if not hosted():
        return AccessState(required=False, granted=True)
    return AccessState(required=True, granted=has_credential(request))


@router.post(ACCESS_PATH.removeprefix("/api/v1"), response_model=AccessState)
def grant_access(payload: Annotated[AccessRequest, Body()], response: Response) -> AccessState:
    """Check the code once and hand this browser the cookie that stands in for it."""
    if not hosted():
        return AccessState(required=False, granted=True)
    code = _access_code()
    if not _same(payload.code.strip(), code):
        raise HTTPException(status_code=401, detail=WRONG_CODE_DETAIL)
    response.set_cookie(
        ACCESS_COOKIE,
        cookie_value(code),
        max_age=COOKIE_MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        samesite="strict",
        secure=(public_origin() or "").startswith("https://"),
    )
    return AccessState(required=True, granted=True)


__all__ = [
    "ACCESS_COOKIE",
    "ACCESS_DETAIL",
    "connection_is_allowed",
    "has_credential",
    "host_is_public",
    "hosted",
    "open_without_credential",
    "public_origin",
    "router",
]
