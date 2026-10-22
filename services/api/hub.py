"""The hub: the WebSocket relay between a runner worker and the Chrome extension.

The popup's Connect call opens a session over HTTP; the extension then dials
``/api/v1/executor/ws`` and the runner worker dials ``/api/v1/runner/ws`` with the same session id.
The hub pairs those two sockets and moves text between them without reading it: a step command is
never interpreted, never re-encoded, never rewritten, and never queued for a peer that is not there.

Every message on the wire is ``{"protocol": 1, "type": "<name>", ...}``. The hub itself sends four
frames -- ``session`` (the extension's first frame, carrying its resume token), ``peer_ready``,
``peer_gone`` and ``hub_error`` -- and refuses a message whose ``protocol`` is not ``1``.

The front door fails closed. A socket must come from the loopback interface and carry a local
``Host`` header, so a DNS name that resolves here is not enough. The extension must present its
exact origin and a live token; the runner must present the configured secret, and an unset
``PROCESSLENS_RUNNER_SECRET`` closes the runner channel to everyone rather than opening it to
everyone.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import APIRouter, Body, Header, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field

from . import access

router = APIRouter(prefix="/api/v1", tags=["hub"])

PROTOCOL_VERSION = 1
SESSION_TTL_SECONDS = 600.0
QUEUE_MAXSIZE = 1024
PROTOCOL_SUBPROTOCOL = "processlens.v1"
TOKEN_SUBPROTOCOL_PREFIX = "pl.token."
EXTENSION_ORIGIN_SCHEME = "chrome-extension://"
EXTENSION_CLIENT = "extension"
ROLE_EXTENSION = "extension"
ROLE_RUNNER = "runner"
SESSION_TOKEN_KIND = "session_token"
RESUME_TOKEN_KIND = "resume_token"

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1", "::ffff:7f00:1"})

# 4401: the caller is not who it claims to be. 4403: it is the wrong kind of caller, or a second
# socket for the same slot. 4400: it is not speaking this protocol. 4408: its own queue overflowed.
# 4409: a person detached it; the session lives on and the extension may reconnect.
UNAUTHORIZED_CODE = 4401
FORBIDDEN_CODE = 4403
PROTOCOL_ERROR_CODE = 4400
QUEUE_OVERFLOW_CODE = 4408
DETACHED_CODE = 4409


# ---------------------------------------------------------------- the three guards


def is_loopback(client_host: str | None) -> bool:
    """True only for the local interface, whatever spelling the socket reports for it."""
    if not client_host:
        return False
    return client_host.strip().lower().strip("[]") in LOOPBACK_HOSTS


def _host_without_port(host_header: str | None) -> str:
    """The host part of a ``Host`` header: lowercased, brackets and any port removed."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.split(":", 1)[0]


def host_header_is_local(host_header: str | None) -> bool:
    """S4: the ``Host`` header must name the local interface, port or no port.

    A browser follows the ``Host`` header, so a DNS name that resolves to this machine would
    otherwise reach the hub from a page and speak to a runner (DNS rebinding).
    """
    if not host_header:
        return False
    return _host_without_port(host_header) in LOCAL_HOSTS


def origin_is_extension(origin: str | None) -> bool:
    """True only for exactly the configured extension's origin, and never for a page."""
    extension_id = os.environ.get("PROCESSLENS_EXTENSION_ID", "").strip()
    if not extension_id or not origin:
        return False
    return origin.strip() == f"{EXTENSION_ORIGIN_SCHEME}{extension_id}"


def _runner_secret() -> str:
    """The configured runner secret. Empty means the runner channel is closed to everyone."""
    return os.environ.get("PROCESSLENS_RUNNER_SECRET", "").strip()


def _bearer_matches(authorization: str | None) -> bool:
    """The bearer check shared by the runner socket and the detach endpoint."""
    secret = _runner_secret()
    if not secret or not authorization:
        return False
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return False
    value = value.strip()
    if not value.isascii():
        return False
    return secrets.compare_digest(value, secret)


def _request_is_local(request: Request) -> bool:
    """Loopback peer and a local ``Host`` header, for the HTTP routes.

    Hosted (PL-086) there is no local peer: the public ``Host`` and an access credential stand in.
    """
    if access.hosted():
        return access.connection_is_allowed(request)
    client = request.client
    host = client.host if client is not None else None
    return is_loopback(host) and host_header_is_local(request.headers.get("host"))


def _socket_is_local(websocket: WebSocket) -> bool:
    """Loopback peer and a local ``Host`` header, for the WebSocket routes.

    Hosted (PL-086), the ``Host`` must be the public address. No access credential is asked here:
    each socket already proves itself -- the extension's with the session token its access-checked
    ``POST /executor/sessions`` returned, the runner's with the runner secret.
    """
    if access.hosted():
        return access.host_is_public(websocket.headers.get("host"))
    client = websocket.client
    host = client.host if client is not None else None
    return is_loopback(host) and host_header_is_local(websocket.headers.get("host"))


def _forbidden(detail: str) -> HTTPException:
    """One 403 for every front-door refusal the HTTP routes make."""
    return HTTPException(status_code=403, detail=detail)


# ---------------------------------------------------------------- the session registry


def _new_queue() -> asyncio.Queue[str]:
    """One socket's outgoing queue."""
    return asyncio.Queue(maxsize=QUEUE_MAXSIZE)


@dataclass
class Peer:
    """One attached socket: its role, its queue, and the two tasks that serve it."""

    role: str
    websocket: WebSocket
    queue: asyncio.Queue[str] = field(default_factory=_new_queue)
    reader: asyncio.Task[None] | None = None
    writer: asyncio.Task[None] | None = None
    gone: bool = False
    # Set once the other side has been told this socket left, so a detach announced by the endpoint
    # and the retire that follows it do not each send a `peer_gone`.
    announced_gone: bool = False


@dataclass
class Session:
    """One extension session: its tokens, its lifetime, and its two attached sockets."""

    session_id: str
    session_token: str
    resume_token: str | None
    tab_url: str
    tab_title: str
    expires_at: float
    # PL-087: the extension install's own id, so a run started from that browser's dashboard tab is
    # driven by that browser's Chrome. Empty for an extension from before PL-087.
    browser_id: str = ""
    claimed: bool = False
    extension: Peer | None = None
    runner: Peer | None = None
    # The token the last COMPLETED attach presented, beside the one that attach issued. The
    # extension may not have read the frame carrying `resume_token` -- `_send_frame` only queues
    # it -- so the token it already holds must stay valid until a later attach completes and
    # supersedes it. At most two resume tokens are ever live: the last used and the last issued.
    # On a first attach the spent session token plays the "used" token's part, so a lost first
    # `session` frame does not lock the extension out either.
    used_token: str | None = None
    # An attach in flight: its token and its claim count only once the first frames are on the
    # wire, so a socket that dies in `accept` leaves the previous token and the slot as they were.
    pending_resume_token: str | None = None
    pending_claim: bool = False


_sessions: dict[str, Session] = {}
_sessions_lock = asyncio.Lock()


def reset_sessions() -> None:
    """Forget every session and rebuild the lock, so tests can start from nothing."""
    global _sessions_lock
    _sessions.clear()
    _sessions_lock = asyncio.Lock()


def _purge_expired(now: float) -> None:
    """Forget every session whose lifetime has run out. The caller holds ``_sessions_lock``.

    A session with a socket still attached is kept whatever its clock says: the lifetime it carries
    is the one it had before that peer dialled in, and it is only renewed once the last one leaves.
    """
    for session_id, row in list(_sessions.items()):
        if _is_expired(row, now) and row.extension is None and row.runner is None:
            del _sessions[session_id]


def _is_expired(session: Session, now: float) -> bool:
    """Whether a session's life has run out (PL-014b requirement 2).

    An UNCLAIMED session expires ``SESSION_TTL_SECONDS`` after the popup created it. A CLAIMED one
    expires ``SESSION_TTL_SECONDS`` after **both** peers have gone: while either is still attached
    the clock is not read at all. Reading it there is what would make a run longer than ten minutes
    lose the re-attach it needs -- and leave the session unpurgeable, because a socket still holds
    it -- so the claimed case answers "live" whenever a peer is present.
    """
    if session.claimed and (session.extension is not None or session.runner is not None):
        return False
    return now >= session.expires_at


# ---------------------------------------------------------------- the frames


def _frame(name: str, **fields: Any) -> dict[str, Any]:
    """One control frame: the envelope every message the hub sends carries."""
    return {"protocol": PROTOCOL_VERSION, "type": name, **fields}


def _encode(frame: dict[str, Any]) -> str:
    """The wire text of one frame."""
    return json.dumps(frame, separators=(",", ":"))


def _hub_error(command_id: str | None) -> dict[str, Any]:
    """The frame that answers a command whose peer is not attached."""
    return _frame("hub_error", code="peer_unavailable", command_id=command_id)


def _is_protocol_message(text: str) -> bool:
    """True when the text is a JSON object that speaks this protocol version."""
    try:
        payload = json.loads(text)
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("protocol") == PROTOCOL_VERSION


def _command_id(text: str) -> str | None:
    """The command id a sender wanted relayed, for the hub_error that answers it."""
    with contextlib.suppress(ValueError):
        payload = json.loads(text)
        if isinstance(payload, dict) and isinstance(payload.get("command_id"), str):
            return payload["command_id"]
    return None


# ---------------------------------------------------------------- the relay


def _other_peer(session: Session, peer: Peer) -> Peer | None:
    """The socket on the far side of the same session, if it is attached."""
    return session.runner if peer.role == ROLE_EXTENSION else session.extension


def _enqueue(peer: Peer, text: str) -> bool:
    """Hand one already-encoded frame to the peer's writer. False when its queue is full."""
    try:
        peer.queue.put_nowait(text)
    except asyncio.QueueFull:
        return False
    return True


async def _send_frame(peer: Peer, frame: dict[str, Any]) -> None:
    """Queue one control frame for a socket. A full queue costs that socket 4408."""
    if not _enqueue(peer, _encode(frame)):
        await _close_peer(peer, QUEUE_OVERFLOW_CODE)


async def _close_peer(peer: Peer, code: int) -> None:
    """Close one socket and stop its tasks; its own relay notices and retires it."""
    peer.gone = True
    with contextlib.suppress(Exception):
        await peer.websocket.close(code=code)
    for task in (peer.reader, peer.writer):
        if task is not None:
            task.cancel()


async def _read_loop(session: Session, peer: Peer) -> None:
    """Relay what one socket sends to its peer verbatim, or answer with hub_error.

    The frame is forwarded exactly as it arrived, so ids, whitespace and key order are the
    sender's, not the hub's. Nothing about a command is interpreted beyond its ``protocol``.

    PL-068: one exception. A reply to a code the *API* sent (``enter_code``) has no runner waiting
    for it -- the run is paused and its worker has gone -- so the hub resolves the pending call
    instead of relaying. The reply never contains the code; only ``entered``/``not_found``.
    """
    while True:
        message = await peer.websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        text = message.get("text")
        if not isinstance(text, str) or not _is_protocol_message(text):
            await _close_peer(peer, PROTOCOL_ERROR_CODE)
            return
        if _resolve_enter_code(text):
            continue
        other = _other_peer(session, peer)
        if other is None:
            await _send_frame(peer, _hub_error(_command_id(text)))
            continue
        if not _enqueue(other, text):
            await _close_peer(other, QUEUE_OVERFLOW_CODE)


# ---------------------------------------------------------------- PL-068: the code a person types

# One pending `enter_code` per command id: what the API waits on while the person's code is being
# typed into the page. It lives in memory only -- the same rule as the code itself.
_pending_codes: dict[str, asyncio.Future[dict[str, Any]]] = {}

ENTER_CODE_TIMEOUT_SECONDS = 15.0


def _resolve_enter_code(text: str) -> bool:
    """Resolve a pending code delivery from the extension's reply; True when it was one."""
    try:
        frame = json.loads(text)
    except (TypeError, ValueError):
        return False
    if not isinstance(frame, dict) or frame.get("type") != "result":
        return False
    command_id = frame.get("command_id")
    if not isinstance(command_id, str):
        return False
    waiter = _pending_codes.pop(command_id, None)
    if waiter is None or waiter.done():
        return False
    observed = frame.get("observed")
    entered = bool(
        frame.get("status") == "ok" and isinstance(observed, dict) and observed.get("entered")
    )
    # The extension's own reason (``not_found``, a refusal's sentence) is kept so the run page can
    # say what happened; it is the extension's words about the page, never the code.
    reason = observed.get("reason") if isinstance(observed, dict) else None
    if not entered and not reason:
        reason = frame.get("error") or frame.get("status")
    waiter.set_result(
        {"entered": entered, "reason": "submitted" if entered else str(reason or "not_found")[:200]}
    )
    return True


async def send_enter_code(
    code: str, *, run_id: str | uuid.UUID, lease_epoch: int = 0
) -> dict[str, Any]:
    """Forward a person's code to the attached extension, once, and wait for what it did.

    The code is carried in this frame and nowhere else: not in a database row, not in a log line,
    not in the answer. The answer says only whether the page's code field was found.
    """
    async with _sessions_lock:
        _purge_expired(time.time())
        session = next(
            (row for row in reversed(_sessions.values()) if row.extension is not None),
            None,
        )
        peer = session.extension if session is not None else None
    if peer is None:
        return {"entered": False, "reason": "no_extension"}

    command_id = f"code:{uuid.uuid4()}"
    loop = asyncio.get_running_loop()
    waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
    _pending_codes[command_id] = waiter
    frame = _frame(
        "enter_code",
        command_id=command_id,
        step_ref="enter_code",
        run_id=str(run_id),
        run_record_id="",
        lease_epoch=lease_epoch,
        expires_at=(time.time() + ENTER_CODE_TIMEOUT_SECONDS) * 1000,
        code=code,
    )
    if not _enqueue(peer, _encode(frame)):
        _pending_codes.pop(command_id, None)
        return {"entered": False, "reason": "no_extension"}
    try:
        return await asyncio.wait_for(waiter, timeout=ENTER_CODE_TIMEOUT_SECONDS)
    except TimeoutError:
        _pending_codes.pop(command_id, None)
        return {"entered": False, "reason": "not_found"}


async def _write_loop(peer: Peer) -> None:
    """Drain one socket's queue to the socket, in order."""
    while True:
        text = await peer.queue.get()
        await peer.websocket.send_text(text)


async def _announce_gone(session: Session, peer: Peer) -> None:
    """Tell the still-attached side that this socket left, exactly once per departure."""
    if peer.announced_gone:
        return
    peer.announced_gone = True
    other = _other_peer(session, peer)
    if other is not None and not other.gone:
        await _send_frame(other, _frame("peer_gone"))


async def _retire(session: Session, peer: Peer) -> None:
    """Forget one socket: free its slot, tell the side still attached, stop its tasks."""
    peer.gone = True
    if session.extension is peer:
        session.extension = None
    if session.runner is peer:
        session.runner = None
    # A claimed session lives for ten minutes after the last peer leaves, not ten minutes from the
    # moment the popup connected, so a run that outlives its creation TTL is still resumable.
    if session.claimed and session.extension is None and session.runner is None:
        session.expires_at = time.time() + SESSION_TTL_SECONDS
    await _announce_gone(session, peer)
    current = asyncio.current_task()
    for task in (peer.reader, peer.writer):
        if task is not None and task is not current:
            task.cancel()
    for task in (peer.reader, peer.writer):
        if task is not None and task is not current:
            with contextlib.suppress(BaseException):
                await task


async def _relay(session: Session, peer: Peer) -> None:
    """Serve one attached socket until either of its two tasks finishes."""
    peer.reader = asyncio.create_task(_read_loop(session, peer), name=f"hub-read-{peer.role}")
    peer.writer = asyncio.create_task(_write_loop(peer), name=f"hub-write-{peer.role}")
    try:
        await asyncio.wait({peer.reader, peer.writer}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        await _retire(session, peer)


async def _refuse(websocket: WebSocket, code: int) -> None:
    """Refuse a socket after reading nothing.

    Starlette wants an ``accept`` before a ``close``, so the socket is accepted and closed at
    once: the caller sees the code and the hub never took a message from it. The accept names the
    offered protocol subprotocol, because a browser whose handshake negotiated no subprotocol
    reports the close as a bare 1006 and the refusal is indistinguishable from a crash.
    """
    offered = _offered_subprotocols(websocket)
    with contextlib.suppress(Exception):
        await websocket.accept(
            subprotocol=PROTOCOL_SUBPROTOCOL if PROTOCOL_SUBPROTOCOL in offered else None
        )
        await websocket.close(code=code)


def _offered_subprotocols(websocket: WebSocket) -> list[str]:
    """The subprotocols the client offered, in the order it offered them."""
    offered = websocket.scope.get("subprotocols") or []
    return [value for value in offered if isinstance(value, str)]


def _offered_token(offered: list[str]) -> str | None:
    """The session token inside the ``pl.token.`` subprotocol, if one was offered."""
    for value in offered:
        if value.startswith(TOKEN_SUBPROTOCOL_PREFIX):
            return value.removeprefix(TOKEN_SUBPROTOCOL_PREFIX)
    return None


def _match_token(session: Session, offered: str, now: float) -> str | None:
    """Which live token the offered value is: the session token while unclaimed, else the resume.

    Returns ``session_token``, ``resume_token``, or ``None`` when neither matches or the session
    has expired. Only the session token of an UNCLAIMED session answers ``session_token``; every
    other live value answers ``resume_token``, because ``pending_claim`` must never be set by a
    re-attach. Three resume values are live and no more: the one the last completed attach issued
    (``resume_token``), the one that attach used (``used_token`` -- the extension may not have read
    the frame that carried the new one) and, while an attach is in flight, the one it is being
    offered (``pending_resume_token``), so the extension may dial with the token the hub just
    handed it. A used token a later attach has superseded is not among them and is refused.
    All comparisons are constant time.
    """
    if _is_expired(session, now) or not offered.isascii():
        return None
    if not session.claimed and secrets.compare_digest(offered, session.session_token):
        return SESSION_TOKEN_KIND
    for live in (session.resume_token, session.used_token, session.pending_resume_token):
        if live is not None and secrets.compare_digest(offered, live):
            return RESUME_TOKEN_KIND
    return None


# ---------------------------------------------------------------- the routes


class SessionCreateRequest(BaseModel):
    """What the popup knows about the tab it is connecting."""

    tab_url: str = ""
    tab_title: str = ""
    # PL-087: the extension install's id. A routing label, not a credential.
    browser_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")] | None = None


@router.post("/executor/sessions", status_code=201)
async def create_executor_session(
    request: Request,
    payload: Annotated[SessionCreateRequest, Body()],
    x_processlens_client: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Open a session and hand back the token the extension connects with, exactly once."""
    if not _request_is_local(request):
        raise _forbidden("the executor API answers only on the local interface")
    if x_processlens_client != EXTENSION_CLIENT:
        raise _forbidden(f"X-ProcessLens-Client must be {EXTENSION_CLIENT}")
    session = Session(
        session_id=str(uuid.uuid4()),
        session_token=secrets.token_urlsafe(32),
        resume_token=None,
        tab_url=payload.tab_url,
        tab_title=payload.tab_title,
        expires_at=time.time() + SESSION_TTL_SECONDS,
        browser_id=payload.browser_id or "",
    )
    async with _sessions_lock:
        _purge_expired(time.time())
        _sessions[session.session_id] = session
    return {
        "session_id": session.session_id,
        "session_token": session.session_token,
        "protocol": PROTOCOL_VERSION,
        "expires_at": session.expires_at,
    }


@router.post("/executor/sessions/{session_id}/close")
async def detach_executor_session(
    request: Request,
    session_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Detach the extension socket. The session lives on, so the extension can reconnect (S6)."""
    if not _request_is_local(request):
        raise _forbidden("the executor API answers only on the local interface")
    if not _bearer_matches(authorization):
        raise _forbidden("the runner secret is required")
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} does not exist")
    peer = session.extension
    if peer is not None:
        session.extension = None
        # Tell the runner, exactly as `_retire` would when a socket drops on its own, and only once:
        # the retire that follows this close must not send a second `peer_gone`. It goes out before
        # the close, so the runner learns at once instead of waiting on a close that may block;
        # without it the runner keeps waiting for a reply that can never come, and the record is
        # parked as a timed-out step instead of the run pausing `executor_disconnected` (R6).
        await _announce_gone(session, peer)
        await _close_peer(peer, DETACHED_CODE)
    return {"detached": True}


@router.get("/executor/session")
async def read_executor_session(request: Request, browser: str | None = None) -> dict[str, Any]:
    """Whether an extension session is attached right now, and which one it is.

    Read-only, so nothing here touches a socket, a frame, a token check or the protocol version.
    The answer comes from the hub's own in-memory registry, read under the same lock the rest of the
    hub uses and after the same expiry purge, so a session whose life has run out is not reported as
    attached. The status lives nowhere else: no database row knows about a Chrome tab.

    The id is the demo supervisor's missing piece (PL-030): it starts a runner worker with
    ``--session <id>``, and this route is the only place the live session's id can be read from
    outside the extension. When several sessions hold sockets, the most recently opened one wins.
    """
    if not _request_is_local(request):
        raise _forbidden("the executor API answers only on the local interface")
    # PL-087: a dashboard tab asks about its own Chrome (`?browser=`), so one person's connected
    # Chrome never tells another person "connected".
    wanted = (browser or "").strip()
    async with _sessions_lock:
        _purge_expired(time.time())
        attached_id = next(
            (
                row.session_id
                for row in reversed(_sessions.values())
                if row.extension is not None and (not wanted or row.browser_id == wanted)
            ),
            None,
        )
    return {"attached": attached_id is not None, "session_id": attached_id}


@router.get("/executor/sessions/attached")
async def list_attached_sessions(request: Request) -> list[dict[str, Any]]:
    """Every session with the extension attached, newest first, with its browser (PL-087).

    The supervisor gives each its own worker, which takes only that browser's runs.
    """
    if not _request_is_local(request):
        raise _forbidden("the executor API answers only on the local interface")
    async with _sessions_lock:
        _purge_expired(time.time())
        return [
            {"session_id": row.session_id, "browser_id": row.browser_id or None}
            for row in reversed(_sessions.values())
            if row.extension is not None
        ]


@router.websocket("/executor/ws")
async def executor_socket(websocket: WebSocket, session: str | None = None) -> None:
    """The extension's socket: one per session, and its first frame carries the resume token."""
    if not _socket_is_local(websocket) or not origin_is_extension(websocket.headers.get("origin")):
        await _refuse(websocket, UNAUTHORIZED_CODE)
        return
    offered = _offered_subprotocols(websocket)
    if PROTOCOL_SUBPROTOCOL not in offered:
        await _refuse(websocket, PROTOCOL_ERROR_CODE)
        return
    token = _offered_token(offered)
    peer = Peer(role=ROLE_EXTENSION, websocket=websocket)
    resume_token = secrets.token_urlsafe(32)
    row: Session | None = None
    refusal: int | None = None
    # The token match and the slot reservation are one critical section, and the slot is reserved
    # before `accept` yields to the event loop. Two sockets that present the same token at the same
    # instant must not both pass, or the second attaches over the first and single use (R5) is gone.
    # Nothing is committed here: the token and the claim take effect only once the first frames are
    # on the wire, so a socket that dies in `accept` cannot cost the extension its session.
    async with _sessions_lock:
        _purge_expired(time.time())
        row = _sessions.get(session or "")
        matched = None if row is None else _match_token(row, token or "", time.time())
        if matched is None:
            refusal = UNAUTHORIZED_CODE
        elif row.extension is not None:
            refusal = FORBIDDEN_CODE
        else:
            row.extension = peer
            row.pending_resume_token = resume_token
            row.pending_claim = matched == SESSION_TOKEN_KIND
    if refusal is not None:
        await _refuse(websocket, refusal)
        return

    attached = False
    try:
        await websocket.accept(subprotocol=PROTOCOL_SUBPROTOCOL)
        runner = row.runner
        await _send_frame(peer, _frame("session", resume_token=resume_token))
        # S5: the pair is only ready when both sides are here, so neither side is told otherwise.
        if runner is not None:
            await _send_frame(peer, _frame("peer_ready"))
            await _send_frame(runner, _frame("peer_ready"))
        # R1: the first frames are on the wire, so this attach is real: publish its token, remember
        # the token it used, and spend the session token if that is what opened the session.
        async with _sessions_lock:
            row.resume_token = row.pending_resume_token
            row.pending_resume_token = None
            # C1: the offered token -- the one the extension actually holds -- stays valid beside
            # the new one, because the frame that carries the new one may never arrive. The next
            # completed attach supersedes it, so exactly two resume tokens are ever live and no
            # history is kept.
            row.used_token = token
            if row.pending_claim:
                row.claimed = True
            row.pending_claim = False
        attached = True
    finally:
        if not attached:
            # R1: the attach never completed, so nothing about it may stick. The slot goes back and
            # the token the extension already holds -- or, on a first attach, its session token --
            # stays the one that opens this session, so the extension may dial again.
            async with _sessions_lock:
                if row.extension is peer:
                    row.extension = None
                    row.pending_resume_token = None
                    row.pending_claim = False
            await _close_peer(peer, UNAUTHORIZED_CODE)
    await _relay(row, peer)


@router.websocket("/runner/ws")
async def runner_socket(websocket: WebSocket, session: str | None = None) -> None:
    """The runner worker's socket: attach to a session the extension is waiting on."""
    if not _socket_is_local(websocket):
        await _refuse(websocket, UNAUTHORIZED_CODE)
        return
    origin = (websocket.headers.get("origin") or "").strip()
    if origin.startswith(EXTENSION_ORIGIN_SCHEME):
        await _refuse(websocket, FORBIDDEN_CODE)
        return
    if not _bearer_matches(websocket.headers.get("authorization")):
        await _refuse(websocket, UNAUTHORIZED_CODE)
        return
    peer = Peer(role=ROLE_RUNNER, websocket=websocket)
    row: Session | None = None
    refusal: int | None = None
    # The expiry re-check and the slot reservation are one critical section, and the slot is
    # reserved before `accept` yields: two runners that race on one session must not both attach.
    # Nothing is committed here: a runner that dies in `accept` leaves the slot to the next one.
    async with _sessions_lock:
        _purge_expired(time.time())
        row = _sessions.get(session or "")
        if row is None or _is_expired(row, time.time()):
            refusal = UNAUTHORIZED_CODE
        elif row.runner is not None:
            refusal = FORBIDDEN_CODE
        else:
            row.runner = peer
    if refusal is not None:
        await _refuse(websocket, refusal)
        return

    attached = False
    try:
        await websocket.accept()
        extension = row.extension
        # S5: the runner waits on `peer_ready` before it claims a run, so it may not be told until
        # the extension is attached, or it claims against a session no extension will ever serve.
        if extension is not None:
            await _send_frame(peer, _frame("peer_ready"))
            await _send_frame(extension, _frame("peer_ready"))
        attached = True
    finally:
        if not attached:
            # R1: an attach that never completed may not hold the slot against the next runner.
            async with _sessions_lock:
                if row.runner is peer:
                    row.runner = None
            await _close_peer(peer, UNAUTHORIZED_CODE)
    await _relay(row, peer)


__all__ = [
    "host_header_is_local",
    "is_loopback",
    "origin_is_extension",
    "reset_sessions",
    "router",
]
