"""Synthetic messy CRM fixture: a stdlib-only web site for end-to-end tests.

The lead-crm fixture is tidy, and the recorder passed on it while failing on
real pages. This site is the opposite on purpose: it is built out of the four
shapes that broke when the owner recorded Gmail and a lead CRM, so a recorder
bug has somewhere local and synthetic to reproduce itself.

* **Ids are regenerated on every page load.** Every interactive element gets a
  React ``:r1:`` id, a Gmail ``:1a`` id or a long hex blob, and the sequence
  moves on with every request. A recorder that trusted an id records a
  selector that dies on the next load; the label, the ``name``, the
  ``aria-label`` and the ``data-testid`` are stable and survive it.
* **The recipient chips are removable and nested.** Each chip is
  ``role="option"`` and clickable, and it contains an ✕
  ``<button type="button" aria-label="Remove <address>">``. The ✕'s own name
  is the target, never the chip's.
* **The notes box is a ``contenteditable``.** Its text is mirrored into a
  hidden input by ``app.js``, because a plain form POST cannot submit it.
* **The save is a full-page POST.** ``POST /save`` answers 303 to
  ``/saved.html``, which server-renders ``<div role="status">Saved</div>``, so
  an observation has to survive a top-level navigation rather than a fetch.

Everything is in memory, there is no network egress, and the data is
synthetic (``@example.test``; the store refuses any other domain). Run it
directly to see the JSON handshake line on stdout::

    python server.py --port 0

Tests use :func:`make_server` and drive the server in-process.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
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

from store import (  # noqa: E402
    DIRECTORY,
    STATIC_CHIP,
    MessyStore,
    is_synthetic,
    parse_recipients,
    search_directory,
)

__all__ = [
    "DEFAULT_SWITCHES",
    "FixtureState",
    "IdMinter",
    "MessyCrmHandler",
    "MessyCrmServer",
    "make_server",
    "main",
    "render_compose",
    "render_notes",
    "render_recipients",
    "render_saved",
    "render_second_page",
    "render_summary",
]

#: Switch defaults. Every one of these is off, so a fresh server is the happy
#: path. The two ``*_ms`` switches are the integers rather than booleans.
DEFAULT_SWITCHES: dict[str, object] = {
    "slow_save_ms": 0,
    "slow_suggest_ms": 0,
    "stale_status": False,
    "reject_recipients": False,
    "suggest_empty": False,
    "listbox_in_flow": False,
}

MAX_BODY = 1 << 20  # 1 MiB is plenty for this fixture and keeps the read bounded.

#: The one address the served markup always shows, so a save needs no script.
PRE_RENDERED_CHIP = STATIC_CHIP

#: The shapes of generated id the templates ask for. React's ``:r1:``, Gmail's
#: ``:1a`` and a long hex blob are the three a real page mixes; a recorder must
#: drop all three from a CSS selector and fall back to a stable rung.
ID_TOKENS: dict[str, str] = {
    "{{ID_COMPOSER}}": "gmail",
    "{{ID_CHIPS}}": "gmail",
    "{{ID_CHIP_ALICE}}": "gmail",
    "{{ID_RECIPIENTS}}": "gmail",
    "{{ID_SAVED_COUNT}}": "gmail",
    "{{ID_TO}}": "react",
    "{{ID_SUGGESTIONS}}": "react",
    "{{ID_ADD}}": "react",
    "{{ID_NOTES}}": "react",
    "{{ID_NOTES_INPUT}}": "react",
    "{{ID_SAVE_FORM}}": "react",
    "{{ID_SUMMARY}}": "react",
    "{{ID_SAVE}}": "digest",
    "{{ID_BACK_LINK}}": "digest",
    "{{ID_SECOND_LINK}}": "digest",
}

ID_TOKEN_RE = re.compile(r"\{\{ID_[A-Z_]+\}\}")
LETTERS = "abcdefghijklmnopqrstuvwxyz"
DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------


class FixtureState:
    """Everything the handler shares: the store, the switches, the counters."""

    def __init__(self) -> None:
        self.store = MessyStore()
        self.switches: dict[str, object] = dict(DEFAULT_SWITCHES)
        self.renders = 0  # pages rendered; the id sequence walks with it
        self.save_posts = 0  # POST /save requests handled
        self.created = 0  # records stored
        self.rejected = 0  # saves refused
        self.suggest_requests = 0  # GET /recipients.json requests handled
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

    # -- the id sequence --------------------------------------------------
    def next_render_seq(self) -> int:
        """One more rendered page, and the sequence its ids are allowed."""
        with self.lock:
            self.renders += 1
            return self.renders

    # -- counters ---------------------------------------------------------
    def note_save_post(self) -> None:
        with self.lock:
            self.save_posts += 1

    def note_saved(self) -> None:
        with self.lock:
            self.created += 1

    def note_rejected(self) -> None:
        with self.lock:
            self.rejected += 1

    def note_suggestion_request(self) -> None:
        with self.lock:
            self.suggest_requests += 1

    def reset(self) -> None:
        self.store.reset()
        with self.lock:
            self.switches = dict(DEFAULT_SWITCHES)
            self.renders = 0
            self.save_posts = 0
            self.created = 0
            self.rejected = 0
            self.suggest_requests = 0

    def state_payload(self) -> dict:
        """The fixture's own truth: what was really saved, and by what."""
        with self.lock:
            switches = dict(self.switches)
            counters = {
                "save_posts": self.save_posts,
                "created": self.created,
                "rejected": self.rejected,
                "renders": self.renders,
                "suggest_requests": self.suggest_requests,
            }
        payload = {
            "switches": switches,
            "static_chip": PRE_RENDERED_CHIP,
            "directory_size": len(DIRECTORY),
        }
        payload.update(counters)
        payload.update(self.store.counts())
        return payload


# ---------------------------------------------------------------------------
# generated ids
# ---------------------------------------------------------------------------


def _base36(number: int) -> str:
    """``1`` -> ``"1"``, ``10`` -> ``"a"``, ``36`` -> ``"10"``."""
    if number <= 0:
        return "0"
    out = ""
    while number:
        number, remainder = divmod(number, 36)
        out = DIGITS[remainder] + out
    return out


class IdMinter:
    """Fresh generated ids for one render, in the shapes real pages emit.

    Gmail's compose runs on React, so its ids look like ``:r1:``; Gmail's own
    ids look like ``:1a``; and a build tool or a UUID puts a long hex blob on a
    container. All three look generated on purpose: a recorder that trusted one
    records a selector that dies on the next load, while the label, the name,
    the ``aria-label`` and the ``data-testid`` are the same on every load.
    """

    def __init__(self, seq: int) -> None:
        self.seq = int(seq)
        self._count = 0

    def _letter(self) -> str:
        self._count += 1
        return LETTERS[(self._count - 1) % len(LETTERS)]

    def react(self) -> str:
        return f":r{self.seq}{self._letter()}:"

    def gmail(self) -> str:
        return f":{_base36(self.seq)}{self._letter()}"

    def digest(self) -> str:
        self._count += 1
        blob = hashlib.sha1(f"messy-crm:{self.seq}:{self._count}".encode()).hexdigest()
        return blob[:16]

    def mint(self, shape: str) -> str:
        if shape == "react":
            return self.react()
        if shape == "gmail":
            return self.gmail()
        if shape == "digest":
            return self.digest()
        raise ValueError(f"unknown id shape: {shape!r}")


def _token(name: str) -> str:
    """The template token for one generated id, e.g. ``_token("TO")``."""
    return "{{ID_" + name + "}}"


def _render_ids(text: str, minter: IdMinter) -> str:
    """Replace every ``{{ID_x}}`` token with a fresh id of the right shape.

    An unknown token raises ``KeyError`` on purpose: a typo in a template must
    fail loudly rather than ship a page with a raw token in it.
    """
    for token in dict.fromkeys(ID_TOKEN_RE.findall(text)):
        text = text.replace(token, minter.mint(ID_TOKENS[token]))
    return text


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------

SWITCH_TOKEN = "<!--SWITCHES-->"


def _attr(value: object) -> str:
    """A value, safe inside a double-quoted attribute."""
    return html.escape(str(value or ""), quote=True)


def _switches_script(state: FixtureState) -> str:
    # "<" is escaped so a switch value can never close the script element.
    blob = json.dumps(state.switches_snapshot(), sort_keys=True).replace("<", "\\u003c")
    return f"<script>window.__SWITCHES__ = {blob};</script>"


def _page(state: FixtureState, name: str) -> str:
    text = (PUBLIC / name).read_text(encoding="utf-8")
    return text.replace(SWITCH_TOKEN, _switches_script(state))


def _status_markup(state: FixtureState) -> str:
    """The stale-status trap, byte-identical to the real status on saved.html.

    With ``stale_status`` on, a page that saves nothing still renders
    ``<div role="status">Saved</div>``, so the markup alone cannot prove a
    save -- only ``/__test__/state`` can. ``app.js`` additionally marks the
    element with ``data-stale-status="true"`` for a test that wants it.
    """
    if state.switches_snapshot().get("stale_status"):
        return '<div role="status">Saved</div>'
    return ""


def _error_markup(error: str) -> str:
    """The refused-save slot: ``role="alert"``, or nothing at all."""
    if not error:
        return ""
    return f'<div role="alert" class="field-error">{html.escape(error)}</div>'


def render_summary(state: FixtureState) -> str:
    """What the saved page says the fixture stored.

    Read back from the store, not from the POST, so the page shows what
    actually landed. Values are escaped: the notes are typed text.
    """
    record = state.store.last()
    if record is None:
        return (
            '<p class="empty" data-testid="nothing-saved">'
            "Nothing has been saved on this server yet.</p>"
        )
    recipients = ", ".join(html.escape(address) for address in record["recipients"])
    notes = html.escape(record["notes"])
    return (
        f'<section class="saved-record" id="{_token("SUMMARY")}" data-testid="saved-summary">'
        "<h2>Recorded</h2>"
        '<p class="saved-line"><span class="saved-key">Recipients:</span> '
        f'<span data-testid="saved-recipients">{recipients or "(none)"}</span></p>'
        '<p class="saved-line"><span class="saved-key">Notes:</span> '
        f'<span data-testid="saved-notes">{notes or "(empty)"}</span></p>'
        '<p class="saved-line"><span class="saved-key">Save number:</span> '
        f'<span id="{_token("SAVED_COUNT")}" data-testid="saved-count">'
        f"{state.store.counts()['saves']}</span></p>"
        "</section>"
    )


def render_compose(state: FixtureState, values: dict | None = None, error: str = "") -> str:
    """index.html: the combobox, the chips, the notes box and the save form.

    ``values`` is the posted form of a refused save, so the page comes back
    with what was typed; a fresh load passes nothing and the hidden
    ``recipients`` input starts at the pre-rendered chip's address.
    """
    values = values or {}
    text = _page(state, "index.html")
    text = text.replace("<!--STATUS-->", _status_markup(state))
    text = text.replace("<!--ERROR-->", _error_markup(error))
    text = text.replace("{{TO_VALUE}}", _attr(values.get("to")))
    text = text.replace("{{NOTES_TEXT}}", html.escape(str(values.get("notes") or "")))
    text = text.replace("{{NOTES_VALUE}}", _attr(values.get("notes")))
    text = text.replace(
        "{{RECIPIENTS_VALUE}}", _attr(values.get("recipients") or PRE_RENDERED_CHIP)
    )
    return _render_ids(text, IdMinter(state.next_render_seq()))


def render_recipients(state: FixtureState, values: dict | None = None, error: str = "") -> str:
    """recipients.html: the chip mess on a page of its own.

    The same composer markup the compose page carries, including the
    pre-rendered ``alice@example.test`` chip, so a recorder unit test can read
    one file that holds nothing but the recipient mess.
    """
    values = values or {}
    text = _page(state, "recipients.html")
    text = text.replace("<!--ERROR-->", _error_markup(error))
    text = text.replace("{{TO_VALUE}}", _attr(values.get("to")))
    text = text.replace(
        "{{RECIPIENTS_VALUE}}", _attr(values.get("recipients") or PRE_RENDERED_CHIP)
    )
    return _render_ids(text, IdMinter(state.next_render_seq()))


def render_notes(state: FixtureState, values: dict | None = None, error: str = "") -> str:
    """notes.html: the contenteditable mess on a page of its own."""
    values = values or {}
    text = _page(state, "notes.html")
    text = text.replace("<!--ERROR-->", _error_markup(error))
    text = text.replace("{{NOTES_TEXT}}", html.escape(str(values.get("notes") or "")))
    text = text.replace("{{NOTES_VALUE}}", _attr(values.get("notes")))
    return _render_ids(text, IdMinter(state.next_render_seq()))


def render_saved(state: FixtureState) -> str:
    """saved.html: the page the save lands on, status and record included.

    ``\"Saved\"`` is in this file's markup, so the status text is there as soon
    as the page loads -- nothing has to run for it to be observable.
    """
    text = _page(state, "saved.html")
    text = text.replace("<!--SUMMARY-->", render_summary(state))
    return _render_ids(text, IdMinter(state.next_render_seq()))


def render_second_page(state: FixtureState) -> str:
    """second-page.html: somewhere to go and, more to the point, to come back from."""
    return _render_ids(_page(state, "second-page.html"), IdMinter(state.next_render_seq()))


#: Which page a refused save comes back on. Each save form posts its own name,
#: so the error lands on the page the person was filling in.
SAVE_PAGES = {
    "compose": render_compose,
    "recipients": render_recipients,
    "notes": render_notes,
}


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


class _FixtureHandler(BaseHTTPRequestHandler):
    server_version = "MessyCrmFixture/1.0"
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

    def _sleep_ms(self, value: object) -> None:
        """A switch-driven delay, capped so a bad patch cannot hang a run."""
        if isinstance(value, int) and value > 0:
            time.sleep(min(value, 30000) / 1000.0)

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


class MessyCrmHandler(_FixtureHandler):
    """The only origin: pages, the save, the suggestions and the test API."""

    # -- GET --------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/__test__/state":
            self._send_json(200, self.state.state_payload())
        elif route in ("/", "/index.html"):
            self._send_html(200, render_compose(self.state))
        elif route in ("/recipients.html", "/recipients"):
            self._send_html(200, render_recipients(self.state))
        elif route in ("/notes.html", "/notes"):
            self._send_html(200, render_notes(self.state))
        elif route in ("/saved.html", "/saved"):
            self._send_html(200, render_saved(self.state))
        elif route in ("/second-page.html", "/second-page"):
            self._send_html(200, render_second_page(self.state))
        elif route == "/recipients.json":
            self._handle_suggestions(parsed)
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

        if route == "/save":
            self._handle_save()
        elif route == "/__test__/reset":
            self.state.reset()
            self._send_json(200, {"ok": True})
        elif route == "/__test__/switches":
            patch = self._read_json()
            if patch is None:
                self._send_json(400, {"error": "body must be a JSON object"})
                return
            for key in ("slow_save_ms", "slow_suggest_ms"):
                if key in patch and (not isinstance(patch[key], int) or patch[key] < 0):
                    self._send_json(400, {"error": f"{key} must be a non-negative integer"})
                    return
            self._send_json(200, {"switches": self.state.merge_switches(patch)})
        else:
            self._not_found()

    # -- the autocomplete ------------------------------------------------
    def _handle_suggestions(self, parsed) -> None:
        state = self.state
        switches = state.switches_snapshot()
        query = parse_qs(parsed.query, keep_blank_values=True).get("q", [None])[0]
        state.note_suggestion_request()
        self._sleep_ms(switches.get("slow_suggest_ms"))

        if switches.get("suggest_empty"):
            # The broken-autocomplete case: typing surfaces nothing at all.
            results: list[dict] = []
        elif query is None:
            results = [dict(entry) for entry in DIRECTORY]
        else:
            results = search_directory(query)
        self._send_json(200, {"query": query or "", "count": len(results), "results": results})

    # -- the save ---------------------------------------------------------
    def _handle_save(self) -> None:
        """A refused save answers the form's own page; a good one redirects.

        The redirect, not a rendered page, is the point: the status has to
        appear on a page the browser navigated to.
        """
        state = self.state
        switches = state.switches_snapshot()
        form = self._read_form()
        state.note_save_post()
        self._sleep_ms(switches.get("slow_save_ms"))

        recipients = parse_recipients(form.get("recipients"))
        notes = form.get("notes") or ""
        render = SAVE_PAGES.get(form.get("page") or "compose", render_compose)

        if switches.get("reject_recipients"):
            state.note_rejected()
            self._send_html(
                200, render(state, form, error="that recipient is not in the directory")
            )
            return

        strangers = [address for address in recipients if not is_synthetic(address)]
        if strangers:
            state.note_rejected()
            self._send_html(
                200,
                render(state, form, error=f"recipients must be @example.test: {strangers[0]}"),
            )
            return

        state.store.record(recipients, notes)
        state.note_saved()
        self._redirect("/saved.html", 303)


class MessyCrmServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, state: FixtureState) -> None:
        self.state = state
        super().__init__(address, handler)


def make_server(port: int = 0, state: FixtureState | None = None) -> MessyCrmServer:
    """Build the fixture server. Tests use it and drive it in-process."""
    return MessyCrmServer(("127.0.0.1", port), MessyCrmHandler, state or FixtureState())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the synthetic messy-crm fixture.")
    parser.add_argument("--port", type=int, default=0, help="port (0 picks a free one)")
    args = parser.parse_args(argv)

    server = make_server(args.port)
    port = server.server_port

    # One line, on stdout, before serving: the same shape the lead-crm fixture
    # prints, so a caller that already parses that one can parse this one.
    print(
        json.dumps(
            {
                "url": f"http://127.0.0.1:{port}",
                "port": port,
                "crm_url": f"http://127.0.0.1:{port}",
                "crm_port": port,
            }
        ),
        flush=True,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
