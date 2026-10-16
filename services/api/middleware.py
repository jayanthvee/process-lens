"""The front door every HTTP route passes through.

The hub has had its own front door since PL-014 (``services.api.hub``); this module is the same
door for every *other* HTTP route, so a page open in a browser cannot drive a local run behind the
operator's back. The reason is CSRF: a cross-site ``POST`` to ``/api/v1/runs/{id}/cancel`` needs no
body and no custom header, so only the ``Host`` and ``Origin`` checks stand between a random web
page and this service.

Four checks, each answering 403 with the check it refused named in ``detail``:

* ``host``: the ``Host`` header must name the local interface, with or without a port, so a DNS
  name that resolves here (DNS rebinding) is refused. The constants and the predicate are the
  hub's -- ``LOCAL_HOSTS`` and ``host_header_is_local`` -- not a second copy of them.
* ``loopback``: the peer must be this machine's loopback interface.
* ``origin``: a state-changing request (``POST``, ``PUT``, ``PATCH``, ``DELETE``) may carry an
  ``Origin`` header only when it is this API's own origin -- the scheme the request was made over
  and the ``Host`` it was asked for, compared without regard to case. An absent ``Origin`` is
  allowed: a CLI, a worker or a test client sends none, and a browser that can send a cross-site
  request always sends one.

  Three routes are the exceptions: ``POST /api/v1/executor/sessions``,
  ``POST /api/v1/executor/version`` and ``POST /api/v1/recipes/compile`` also accept exactly
  ``chrome-extension://{PROCESSLENS_EXTENSION_ID}`` -- the pinned id the hub uses for its socket.
  The extension's service worker opens that session and reports its own manifest version from its
  own origin and has no other one to present, and the popup compiles the recording it just captured
  from that same origin, because the popup has none other to present either. An unset
  ``PROCESSLENS_EXTENSION_ID`` accepts no extension origin at all, and every other route accepts
  only this API's own origin.
* ``content-type``: a state-changing request with a body must declare ``application/json`` (its
  parameters are ignored, so ``application/json; charset=utf-8`` is fine). A browser cannot send
  that cross-origin without a preflight, and this API sends no CORS headers, so the preflight
  fails and the request is never sent. A request with no body is fine, whatever it declares.

  ``POST /api/v1/batches`` is the one exception: a spreadsheet upload is a file, so that path also
  accepts ``multipart/form-data``. ``multipart/form-data`` is a CORS-safelisted media type, so a
  cross-site form could send one without a preflight -- but it cannot hide its ``Origin``, and the
  origin check above refuses every ``Origin`` that is not this API's own. The exception is one path
  wide for that reason, and every other state-changing request keeps the JSON-only rule.

A non-``http`` scope passes straight through, so ``/api/v1/executor/ws`` and ``/api/v1/runner/ws``
keep their own, stricter checks: the hub refuses a socket by close code, not by 403.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .hub import host_header_is_local, is_loopback, origin_is_extension

STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
JSON_MEDIA_TYPE = "application/json"
# The spreadsheet upload reads a file, so ``POST /api/v1/batches`` is the one route that takes a
# form body. Every other state-changing request still has to be JSON, which a browser cannot send
# cross-origin without a preflight.
FORM_MEDIA_TYPE = "multipart/form-data"
UPLOAD_PATH = "/api/v1/batches"
FORBIDDEN_STATUS = 403
# The three routes a browser page never calls and the extension does, so the three routes where an
# origin other than this API's own is meaningful: the service worker opens a session and reports its
# manifest version, and the popup compiles the recording it just captured. All accept the pinned id
# and nothing else.
EXTENSION_SESSION_PATH = "/api/v1/executor/sessions"
EXTENSION_VERSION_PATH = "/api/v1/executor/version"
EXTENSION_COMPILE_PATH = "/api/v1/recipes/compile"
EXTENSION_ORIGIN_PATHS = frozenset(
    {EXTENSION_SESSION_PATH, EXTENSION_VERSION_PATH, EXTENSION_COMPILE_PATH}
)

HOST_DETAIL = "refused by the host check: Host must be 127.0.0.1:<port> or localhost:<port>"
LOOPBACK_DETAIL = "refused by the loopback check: the client is not this machine's loopback address"
ORIGIN_DETAIL = "refused by the origin check: Origin must be this API's own origin"
CONTENT_TYPE_DETAIL = (
    "refused by the content-type check: a request with a body must be application/json"
)


def _host_refusal(request: Request) -> str | None:
    """The ``Host`` header must name the local interface (DNS rebinding)."""
    if host_header_is_local(request.headers.get("host")):
        return None
    return HOST_DETAIL


def _loopback_refusal(request: Request) -> str | None:
    """The peer must be the local interface, so a remote caller never reaches the API at all."""
    client = request.client
    if is_loopback(client.host if client is not None else None):
        return None
    return LOOPBACK_DETAIL


def _own_origin(request: Request) -> str:
    """This API's own origin: the scheme it was reached over and the ``Host`` it was asked for.

    The page the API serves is on that origin, so its ``Origin`` header is exactly this string. The
    comparison is case-insensitive because a scheme and a host name are.
    """
    return f"{request.url.scheme}://{request.headers.get('host', '').strip()}"


def _origin_refusal(request: Request) -> str | None:
    """Refuse a state-changing request whose ``Origin`` is not this API's own origin."""
    if request.method not in STATE_CHANGING_METHODS:
        return None
    origin = request.headers.get("origin")
    if origin is None:
        return None  # a CLI, a worker or a test client sends none
    if origin.strip().lower() == _own_origin(request).lower():
        return None
    # Opening a session, reporting a version and compiling a recording are the three things the
    # extension does over HTTP, and it can present no origin but its own `chrome-extension://<id>`.
    # Only those paths, and only the pinned id: every other route and every other extension id is
    # refused as before. `origin_is_extension` owns the id comparison and answers False when no id
    # is configured.
    if request.url.path.rstrip("/") in EXTENSION_ORIGIN_PATHS and origin_is_extension(origin):
        return None
    return ORIGIN_DETAIL


def _declares_a_body(request: Request) -> bool:
    """True when the headers alone say a body is coming."""
    if "transfer-encoding" in request.headers:
        return True
    length = request.headers.get("content-length", "").strip()
    return length.isdigit() and int(length) > 0


def _content_type_refusal(request: Request, *, body: bool) -> str | None:
    """Refuse a body that is not JSON, so a browser has to preflight (and the preflight fails)."""
    if request.method not in STATE_CHANGING_METHODS:
        return None
    if not (body or _declares_a_body(request)):
        return None
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type == JSON_MEDIA_TYPE:
        return None
    if media_type == FORM_MEDIA_TYPE and request.url.path.rstrip("/") == UPLOAD_PATH:
        return None
    return CONTENT_TYPE_DETAIL


def _needs_a_body_peek(request: Request) -> bool:
    """Whether only the body itself can tell us this state-changing request carries one."""
    return request.method in STATE_CHANGING_METHODS and not _declares_a_body(request)


def _replaying(held: list[Message], receive: Receive) -> Receive:
    """A ``receive`` that hands back the message a body peek took, then the stream itself."""

    async def replay() -> Message:
        return held.pop(0) if held else await receive()

    return replay


class FrontDoorMiddleware:
    """Refuse an HTTP request that is not the local page talking to the local API."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # A websocket (or a lifespan message) is not this module's business: the hub's own
            # checks are stricter, and they refuse by close code rather than by 403.
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        # A body no header announces can only be seen by reading the first message. The peek is put
        # back in front of the stream, so the route still receives everything, in order.
        held: list[Message] = []
        detail = _host_refusal(request) or _loopback_refusal(request) or _origin_refusal(request)
        if detail is None and _needs_a_body_peek(request):
            held.append(await receive())
        if detail is None:
            detail = _content_type_refusal(request, body=bool(held and held[0].get("body")))

        if detail is not None:
            refusal = JSONResponse({"detail": detail}, status_code=FORBIDDEN_STATUS)
            await refusal(scope, receive, send)
            return

        await self.app(scope, _replaying(held, receive), send)


__all__ = ["FrontDoorMiddleware"]
