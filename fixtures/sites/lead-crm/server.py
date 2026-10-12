"""Synthetic lead CRM fixture: a stdlib-only web site for end-to-end tests.

Two origins, on purpose (the off-origin test needs a second one):

* the CRM origin, served by :class:`LeadCrmHandler` -- pages, the
  ``POST /leads/new`` save, and the ``/__test__`` control API;
* the partner origin, served by :class:`PartnerHandler` on a *different*
  port.

Everything is in memory, there is no network egress, and the data is
synthetic. Run it directly to see the JSON handshake line on stdout::

    python server.py --port 0

Tests use :func:`make_server` and drive the server in-process.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
PUBLIC = HERE / "public"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from store import LeadStore  # noqa: E402

__all__ = [
    "DEFAULT_SWITCHES",
    "FixtureState",
    "LeadCrmHandler",
    "LeadCrmServer",
    "PartnerHandler",
    "make_partner_server",
    "make_server",
    "main",
    "render_index",
    "render_new_lead",
]

#: Switch defaults. Every one of these is off, so a fresh server is the
#: happy path. ``slow_save_ms`` is the one integer rather than a boolean.
DEFAULT_SWITCHES: dict[str, object] = {
    "slow_save_ms": 0,
    "stale_toast": False,
    "login_page": False,
    "challenge_page": False,
    "field_in_iframe": False,
    "disconnect_mid_save": False,
    "required_company": False,
}

MAX_BODY = 1 << 20  # 1 MiB is plenty for this fixture and keeps the read bounded.


class FixtureState:
    """Everything the handler shares: the store, the switches, the counters."""

    def __init__(self) -> None:
        self.store = LeadStore()
        self.switches: dict[str, object] = dict(DEFAULT_SWITCHES)
        self.saves = 0  # save attempts that reached the server (POST /leads/new + mark-save)
        self.save_posts = 0  # POST /leads/new requests handled
        self.mark_save_calls = 0  # POST /__test__/mark-save calls
        self.created = 0
        self.duplicates = 0
        self.rejected = 0
        self.disconnect_pending = False
        self.partner_origin = ""
        self.lock = threading.Lock()

    # -- switches ---------------------------------------------------------
    def switches_snapshot(self) -> dict[str, object]:
        with self.lock:
            return dict(self.switches)

    def merge_switches(self, patch: dict) -> dict[str, object]:
        with self.lock:
            for key, value in patch.items():
                self.switches[key] = value
            return dict(self.switches)

    def clear_switches(self) -> None:
        with self.lock:
            self.switches = dict(DEFAULT_SWITCHES)

    # -- counters ---------------------------------------------------------
    def note_save_post(self) -> None:
        with self.lock:
            self.save_posts += 1
            self.saves += 1

    def note_mark_save(self) -> int:
        with self.lock:
            self.mark_save_calls += 1
            self.saves += 1
            return self.saves

    def note_saved(self, disconnect_pending: bool) -> None:
        with self.lock:
            self.created += 1
            if disconnect_pending:
                self.disconnect_pending = True

    def note_duplicate(self) -> None:
        with self.lock:
            self.duplicates += 1

    def note_rejected(self) -> None:
        with self.lock:
            self.rejected += 1

    def reset(self) -> None:
        self.store.reset()
        with self.lock:
            self.switches = dict(DEFAULT_SWITCHES)
            self.saves = 0
            self.save_posts = 0
            self.mark_save_calls = 0
            self.created = 0
            self.duplicates = 0
            self.rejected = 0
            self.disconnect_pending = False

    def state_payload(self) -> dict:
        with self.lock:
            switches = dict(self.switches)
            disconnect_pending = self.disconnect_pending
            payload = {
                "leads": self.store.counts(),
                "saves": self.saves,
                "switches": switches,
                "disconnect_pending": disconnect_pending,
                "save_posts": self.save_posts,
                "mark_save_calls": self.mark_save_calls,
                "created": self.created,
                "duplicates": self.duplicates,
                "rejected": self.rejected,
            }
        return payload


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------

SWITCH_TOKEN = "<!--SWITCHES-->"


def _switches_script(state: FixtureState) -> str:
    # "<" is escaped so a switch value can never close the script element.
    blob = json.dumps(state.switches_snapshot(), sort_keys=True).replace("<", "\\u003c")
    return f"<script>window.__SWITCHES__ = {blob};</script>"


def _page(state: FixtureState, name: str) -> str:
    text = (PUBLIC / name).read_text(encoding="utf-8")
    return text.replace(SWITCH_TOKEN, _switches_script(state))


def render_results(results: list[dict], submitted: bool) -> str:
    """The search results region: an ARIA table, or the empty message.

    ``submitted`` tells "not searched yet" (no region at all) from "searched
    and found nothing" (the brief's ``No leads found``).
    """
    if not results:
        if not submitted:
            return ""
        return '<p class="empty" data-testid="no-results">No leads found</p>'
    rows = []
    for lead in results:
        rows.append(
            '<div role="row" class="result-row" data-lead-row>'
            f'<div role="cell" class="result-cell">{html.escape(lead["email"])}</div>'
            f'<div role="cell" class="result-cell">{html.escape(lead["interest"])}</div>'
            f'<div role="cell" class="result-cell">{html.escape(lead["company"])}</div>'
            "</div>"
        )
    head = (
        '<div role="rowgroup" class="results-head">'
        '<div role="row">'
        '<div role="columnheader" class="result-cell">Email</div>'
        '<div role="columnheader" class="result-cell">Interest</div>'
        '<div role="columnheader" class="result-cell">Company</div>'
        "</div></div>"
    )
    body = '<div role="rowgroup" class="results-body">' + "".join(rows) + "</div>"
    return (
        '<div role="table" aria-label="Search results" class="results" data-testid="lead-results">'
        + head
        + body
        + "</div>"
    )


def render_index(state: FixtureState, query: str | None = None, submitted: bool = False) -> str:
    """index.html with the results region and the partner link filled in."""
    text = _page(state, "index.html")
    results = state.store.search(query or "")
    text = text.replace("<!--RESULTS-->", render_results(results, submitted))
    partner = state.partner_origin or "about:blank"
    text = text.replace(
        "<!--PARTNER_LINK-->",
        f'<a id="partner-link" class="button-link" href="{html.escape(partner)}/partner.html">'
        "Open partner site</a>",
    )
    value = "" if query is None else html.escape(query, quote=True)
    text = text.replace("{{Q}}", value)
    return text


def _email_field(state: FixtureState, email: str = "") -> str:
    """The Email control: inline, or inside a same-origin iframe (switch)."""
    inline = (
        '<label for="lead-email">Email</label>'
        '<input id="lead-email" name="email" type="text" data-testid="lead-email"'
        f' autocomplete="off" value="{html.escape(email or "", quote=True)}">'
    )
    if not state.switches_snapshot().get("field_in_iframe"):
        return inline
    inner = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Email</title>'
        "<style>body{margin:8px;font:14px system-ui,sans-serif}"
        "label{display:block}input{display:block;width:280px;height:32px}</style></head><body>"
        + inline
        + "</body></html>"
    )
    srcdoc = html.escape(inner, quote=True)
    return (
        '<iframe id="lead-email-frame" class="field-frame" title="Email field" '
        f'srcdoc="{srcdoc}"></iframe>'
        '<input id="lead-email-bridge" name="email" type="hidden" value="">'
    )


def render_new_lead(
    state: FixtureState,
    values: dict | None = None,
    toast: str = "",
    error: str = "",
) -> str:
    """new-lead.html with the toast, the field error and the Email field."""
    values = values or {}
    text = _page(state, "new-lead.html")
    switches = state.switches_snapshot()

    if toast:
        # Exactly the markup the brief names for a real save.
        pieces = f'<div role="status">{html.escape(toast)}</div>'
    elif switches.get("stale_toast"):
        # Deliberately byte-identical to a real toast: on its own the page
        # cannot prove a save, so only /__test__/state can.
        pieces = '<div role="status">Lead created</div>'
    else:
        pieces = ""
    text = text.replace("<!--TOAST-->", pieces)

    text = text.replace(
        "<!--FIELD_ERROR-->",
        f'<div role="alert" class="field-error">{html.escape(error)}</div>' if error else "",
    )
    text = text.replace("<!--EMAIL_FIELD-->", _email_field(state, str(values.get("email") or "")))
    text = text.replace("<!--SWITCHES-->", _switches_script(state))

    # A rejected form comes back with what was already typed, so a person (or a
    # test) can correct one field instead of retyping the whole record.
    text = text.replace("{{COMPANY_VALUE}}", html.escape(str(values.get("company") or ""), quote=True))
    selected = " selected" if (values.get("interest") or "") == "Data course" else ""
    text = text.replace("{{DATA_SELECTED}}", selected)
    return text


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


class _FixtureHandler(BaseHTTPRequestHandler):
    server_version = "LeadCrmFixture/1.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    @property
    def state(self) -> FixtureState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib signature
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_html(self, status: int, text: str) -> None:
        self._send(status, text.encode("utf-8"), "text/html; charset=utf-8")

    def _send_json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _redirect(self, location: str, status: int = 302) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        return self.rfile.read(min(length, MAX_BODY))

    def _read_json(self) -> dict | None:
        raw = self._read_body()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _read_form(self) -> dict[str, str]:
        raw = self._read_body().decode("utf-8", "replace")
        parsed = parse_qs(raw, keep_blank_values=True)
        return {key: values[0] if values else "" for key, values in parsed.items()}

    def _static(self, name: str, content_type: str) -> None:
        path = PUBLIC / name
        if not path.is_file():
            self._not_found()
            return
        self._send(200, path.read_bytes(), content_type)

    def _not_found(self) -> None:
        self._send_html(
            404,
            "<!DOCTYPE html><html><head><title>Not found</title></head>"
            "<body><h1>Not found</h1></body></html>",
        )


class LeadCrmHandler(_FixtureHandler):
    """The CRM origin: pages, the save, and the /__test__ control API."""

    # -- GET --------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=True).get("q", [None])[0]

        if route == "/__test__/state":
            self._send_json(200, self.state.state_payload())
        elif route in ("/", "/index.html"):
            self._send_html(200, render_index(self.state, query, submitted="q" in parsed.query))
        elif route == "/search.json":
            results = self.state.store.search(query or "")
            self._send_json(200, {"query": query or "", "count": len(results), "results": results})
        elif route in ("/new-lead.html", "/leads/new"):
            self._send_html(200, render_new_lead(self.state))
        elif route == "/login.html":
            self._send_html(200, _page(self.state, "login.html"))
        elif route == "/challenge.html":
            self._send_html(200, _page(self.state, "challenge.html"))
        elif route == "/app.js":
            self._static("app.js", "text/javascript; charset=utf-8")
        elif route == "/styles.css":
            self._static("styles.css", "text/css; charset=utf-8")
        elif route == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._not_found()

    # -- POST -------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        route = urlparse(self.path).path

        if route == "/leads/new":
            self._handle_save()
        elif route == "/login.html":
            # The login form is never submitted by any test, but a stray submit
            # must not look like a crash.
            self._read_body()
            self._send_html(200, _page(self.state, "login.html"))
        elif route == "/__test__/reset":
            self.state.reset()
            self._send_json(200, {"ok": True})
        elif route == "/__test__/switches":
            patch = self._read_json()
            if patch is None:
                self._send_json(400, {"error": "body must be a JSON object"})
                return
            if "slow_save_ms" in patch and (
                not isinstance(patch["slow_save_ms"], int) or patch["slow_save_ms"] < 0
            ):
                self._send_json(400, {"error": "slow_save_ms must be a non-negative integer"})
                return
            self._send_json(200, {"switches": self.state.merge_switches(patch)})
        elif route == "/__test__/mark-save":
            self._send_json(200, {"saves": self.state.note_mark_save()})
        else:
            self._not_found()

    # -- the save ---------------------------------------------------------
    def _handle_save(self) -> None:
        state = self.state
        switches = state.switches_snapshot()
        form = self._read_form()
        state.note_save_post()

        slow_ms = switches.get("slow_save_ms") or 0
        if isinstance(slow_ms, int) and slow_ms > 0:
            time.sleep(min(slow_ms, 30000) / 1000.0)

        if switches.get("login_page"):
            state.note_rejected()
            self._redirect("/login.html", 302)
            return

        if switches.get("challenge_page"):
            state.note_rejected()
            self._send_html(200, _page(state, "challenge.html"))
            return

        email = (form.get("email") or "").strip()
        interest = form.get("interest") or ""
        company = form.get("company") or ""

        if not email:
            state.note_rejected()
            self._send_html(200, render_new_lead(state, form, error="email is required"))
            return

        if switches.get("required_company") and not company.strip():
            state.note_rejected()
            self._send_html(200, render_new_lead(state, form, error="company is required"))
            return

        if state.store.create(email, interest, company):
            state.note_saved(bool(switches.get("disconnect_mid_save")))
            self._send_html(200, render_new_lead(state, form, toast="Lead created"))
        else:
            state.note_duplicate()
            self._send_html(200, render_new_lead(state, form, error="that email already exists"))


class PartnerHandler(_FixtureHandler):
    """The partner origin: one plain page, and nothing else."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        route = urlparse(self.path).path
        if route in ("/", "/partner.html"):
            self._send_html(200, _page(self.state, "partner.html"))
        elif route == "/styles.css":
            self._static("styles.css", "text/css; charset=utf-8")
        elif route == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._not_found()

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        self._not_found()


class LeadCrmServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, state: FixtureState) -> None:
        self.state = state
        super().__init__(address, handler)


def make_partner_server(port: int = 0, state: FixtureState | None = None) -> ThreadingHTTPServer:
    """The partner origin. Its own server, so its port differs from the CRM's.

    It carries the shared state, because the partner page is rendered through
    the same ``_page`` helper (it needs the switches).
    """
    server = ThreadingHTTPServer(("127.0.0.1", port), PartnerHandler)
    server.state = FixtureState() if state is None else state  # type: ignore[attr-defined]
    return server


def make_server(port: int = 0, partner_port: int | None = None) -> LeadCrmServer:
    """Build the CRM server, plus a partner origin on a different port.

    ``partner_port=None`` means "pick a free port"; a partner origin is always
    created because ``index.html`` links to it. The partner server is attached
    as ``server.partner_server`` and the shared state as ``server.state``.
    """
    state = FixtureState()
    partner = make_partner_server(0 if partner_port is None else partner_port, state)
    state.partner_origin = f"http://127.0.0.1:{partner.server_port}"
    server = LeadCrmServer(("127.0.0.1", port), LeadCrmHandler, state)
    server.partner_server = partner  # type: ignore[attr-defined]
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the synthetic lead-crm fixture (CRM origin + partner origin)."
    )
    parser.add_argument("--port", type=int, default=0, help="CRM port (0 picks a free one)")
    parser.add_argument(
        "--partner-port", type=int, default=0, help="partner port (0 picks a free one)"
    )
    args = parser.parse_args(argv)

    server = make_server(args.port, args.partner_port)
    partner = server.partner_server  # type: ignore[attr-defined]

    print(
        json.dumps(
            {
                "crm_url": f"http://127.0.0.1:{server.server_port}",
                "partner_url": f"http://127.0.0.1:{partner.server_port}",
                "crm_port": server.server_port,
                "partner_port": partner.server_port,
            }
        ),
        flush=True,
    )

    threading.Thread(target=partner.serve_forever, name="partner-origin", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        partner.shutdown()
        partner.server_close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
