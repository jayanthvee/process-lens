#!/usr/bin/env python
"""Standalone proof that the widget-corpus fixture works.

Run it directly (not through pytest)::

    python fixtures/sites/widget-corpus/selftest.py

It starts the server in-process, exercises every route, the ``/__test__``
control API and every switch, prints one line per check, then
``SELFTEST PASSED (N checks)`` or ``SELFTEST FAILED`` and exits 0 or 1.

Three kinds of check live here, and all three are the point of the corpus:

* the **DOM promises** each page makes -- the combobox's ``aria-controls`` must
  point at the listbox id, the chips container must be a labelled listbox whose
  ✕ carries ``aria-label="Remove <address>"``, the body editor must be
  ``contenteditable`` whose served script writes ``textContent`` directly and
  never dispatches an ``input`` event, the shadow page must nest a real **open**
  shadow root, the iframe page must embed a same-origin frame, and the perf page
  must generate exactly 1000 fields;
* the **store and switch behaviour** -- a send stores a message, a save stores a
  widget's value, and the switches reach the page;
* the two **compile refusals** (R1's key send, R2's chip removal), exercised
  through ``packages.compiler.compile_recording`` with a small hand-written
  recording shaped like the recorder's envelope.
"""

from __future__ import annotations

import http.client
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import server  # noqa: E402
from store import (  # noqa: E402
    DIRECTORY,
    WidgetStore,
    is_synthetic,
    parse_recipients,
    search_directory,
)

FAILURES: list[str] = []
CHECKS = 0

ALICE = "alice@example.test"
BOB = "bob@example.test"
MARY = "mary.an@example.test"
BODY = "Hello from the widget corpus"
SECOND_BODY = "Second message, typed into the script-owned editor"

#: Every page route, so the loop checks below cover all of them.
PAGE_ROUTES = (
    "/",
    "/index.html",
    "/gmail-compose.html",
    "/gmail-compose",
    "/plain-editor.html",
    "/plain-editor",
    "/framework-editor.html",
    "/framework-editor",
    "/react-autocomplete.html",
    "/react-autocomplete",
    "/shadow-form.html",
    "/shadow-form",
    "/iframe-form.html",
    "/iframe-form",
    "/chip-removal.html",
    "/chip-removal",
    "/perf.html",
    "/perf",
    "/iframe-child.html",
)

#: The widget pages themselves, linked from the index and linking back.
WIDGET_FILES = (
    "gmail-compose.html",
    "plain-editor.html",
    "framework-editor.html",
    "react-autocomplete.html",
    "shadow-form.html",
    "iframe-form.html",
    "chip-removal.html",
    "perf.html",
)

ASSETS = (
    "/app.js",
    "/compose-app.js",
    "/editors.js",
    "/autocomplete.js",
    "/shadow.js",
    "/styles.css",
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")

#: The refusal the compile must show for a recording that ends with a key send.
KEY_REFUSAL = "The save or send was done with a key."
#: The refusal the compile must show for a chip removed with no control.
CHIP_REFUSAL = "A recipient was removed in a way that can't be replayed"


def check(name: str, ok: bool, detail: str = "") -> bool:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"OK   {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))
    return ok


def raw(base: str, path: str, method: str = "GET", body=None, content_type: str | None = None):
    """One request, no redirect following, so every status is observable."""
    parsed = urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=15)
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type
    payload = body
    if isinstance(body, str):
        payload = body.encode("utf-8")
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    text = response.read().decode("utf-8", "replace")
    result = {
        "status": response.status,
        "headers": {k.lower(): v for k, v in response.getheaders()},
        "body": text,
    }
    conn.close()
    return result


def json_response(response):
    return json.loads(response["body"])


def state(base: str) -> dict:
    return json_response(raw(base, "/__test__/state"))


def reset(base: str) -> dict:
    return json_response(raw(base, "/__test__/reset", "POST"))


def set_switches(base: str, **patch):
    return raw(base, "/__test__/switches", "POST", json.dumps(patch), "application/json")


def post_send(base: str, payload: dict):
    return raw(base, "/send", "POST", json.dumps(payload), "application/json")


def post_save(base: str, payload: dict):
    return raw(base, "/save", "POST", json.dumps(payload), "application/json")


def load_compiler():
    """The compiler's entry points, or a reason string. Never raises."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from packages.compiler import (  # noqa: PLC0415
            CompileError,
            CompileOptions,
            compile_recording,
        )

        return CompileOptions, CompileError, compile_recording, ""
    except Exception as error:  # a missing or broken package is a FAIL, not a crash
        try:
            from packages.compiler.compiler import (  # noqa: PLC0415
                CompileOptions,
                compile_recording,
            )
            from packages.compiler.errors import CompileError  # noqa: PLC0415

            return CompileOptions, CompileError, compile_recording, ""
        except Exception as fallback:
            return None, None, None, f"{type(fallback).__name__}: {fallback} [first: {error}]"


def refusal_message(error) -> str:
    """The problem text, whichever attribute the compiler puts it in."""
    parts = [str(error)]
    parts.extend(str(problem) for problem in getattr(error, "problems", []) or [])
    return " ".join(parts)


def compile_and_expect_refusal(compile_recording, CompileError, options, recording) -> tuple:
    """Compile a recording that must be refused. Returns (ok, detail)."""
    try:
        compile_recording(recording, options)
    except CompileError as error:
        return True, refusal_message(error)
    except Exception as error:  # noqa: BLE001 - any other error is a failure detail
        return False, f"{type(error).__name__}: {error}"
    return False, "the compiler compiled the recording instead of refusing it"


def main() -> int:
    # The check names are ASCII, but page markup carries ✕ and —. A cp1252
    # console must not turn a FAIL into a crash.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # --- 0. the standalone handshake line -------------------------------
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "server.py"), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=str(HERE),
        text=True,
    )
    line = ""
    try:
        line = proc.stdout.readline().strip()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    try:
        handshake = json.loads(line)
    except json.JSONDecodeError:
        handshake = None
    check(
        "server.py prints one JSON handshake line on stdout",
        isinstance(handshake, dict)
        and set(handshake) == {"url", "port", "crm_url", "crm_port"}
        and handshake["url"] == f"http://127.0.0.1:{handshake['port']}"
        and handshake["port"] == handshake["crm_port"]
        and handshake["url"] == handshake["crm_url"],
        f"line={line!r}",
    )

    # --- 0b. store unit checks ------------------------------------------
    store = WidgetStore()
    check(
        "store.send stores the recipients, the body and the page it came from",
        store.send([ALICE], BODY) == {"to": [ALICE], "body": BODY, "source": "gmail-compose"},
    )
    check(
        "store.last_message reads the newest message back",
        (store.last_message() or {}).get("body") == BODY,
    )
    store.send([BOB, ALICE], SECOND_BODY, "chip-removal")
    check(
        "store.recipients counts every address, and message_bodies lists every body",
        store.recipients() == {ALICE: 2, BOB: 1} and store.message_bodies() == [BODY, SECOND_BODY],
    )
    check(
        "store.save merges a widget's fields and values() reads them back",
        store.save("shadow-form", {"name": "Alice", "team": "sales"})
        == {"name": "Alice", "team": "sales"}
        and store.values("shadow-form")["team"] == "sales"
        and store.values()["shadow-form"]["name"] == "Alice",
    )
    check(
        "store.counts reports the shape /__test__/state serves",
        store.counts()["sends"] == 2
        and store.counts()["saves"] == 1
        and len(store.counts()["messages"]) == 2
        and store.counts()["values"]["shadow-form"]["name"] == "Alice",
    )
    check(
        "parse_recipients splits, trims and de-duplicates",
        parse_recipients(f" {ALICE} ,{BOB}, {ALICE} ,") == [ALICE, BOB],
    )
    check(
        "is_synthetic accepts only @example.test addresses",
        is_synthetic(ALICE)
        and not is_synthetic("dana@example.com")
        and not is_synthetic("@example.test")
        and not is_synthetic(""),
    )
    check(
        "search_directory matches a name or an address, and an empty query matches nothing",
        [entry["address"] for entry in search_directory("ma")] == [MARY]
        and search_directory("") == []
        and len(search_directory("example.test")) == 5,
    )
    check("store.reset empties the store", (store.reset() or store.counts())["messages"] == [])
    check(
        "store.reset clears the counters too",
        store.counts() == {"messages": [], "sends": 0, "saves": 0, "values": {}},
    )

    # --- start the fixture in-process -----------------------------------
    fixture = server.make_server(0)
    base = f"http://127.0.0.1:{fixture.server_port}"
    threading.Thread(target=fixture.serve_forever, name="widget-corpus", daemon=True).start()
    check(
        "make_server binds a loopback port",
        fixture.server_port > 0 and base.startswith("http://127.0.0.1:"),
    )

    try:
        # --- 1. every route answers 200 ----------------------------------
        statuses = {route: raw(base, route)["status"] for route in PAGE_ROUTES}
        check(
            "every page route, long and short, answers 200",
            all(status == 200 for status in statuses.values()),
            f"{ {route: status for route, status in statuses.items() if status != 200} }",
        )
        assets = {asset: raw(base, asset) for asset in ASSETS}
        check(
            "every static asset answers 200 with its own content type",
            all(response["status"] == 200 for response in assets.values())
            and assets["/styles.css"]["headers"].get("content-type") == "text/css; charset=utf-8"
            and all(
                assets[asset]["headers"].get("content-type") == "text/javascript; charset=utf-8"
                for asset in ASSETS
                if asset != "/styles.css"
            ),
            f"assets={ {asset: response['status'] for asset, response in assets.items()} }",
        )
        check(
            "every page is UTF-8 HTML",
            all(
                raw(base, route)["headers"].get("content-type") == "text/html; charset=utf-8"
                for route in PAGE_ROUTES
            ),
        )
        check(
            "unknown routes answer 404 (GET and POST) and favicon answers 204",
            raw(base, "/nope")["status"] == 404
            and raw(base, "/nope", "POST")["status"] == 404
            and raw(base, "/favicon.ico")["status"] == 204,
        )

        # --- 2. the index links every widget, and every widget links back
        index = raw(base, "/")["body"]
        check(
            "the index links every widget page",
            all(f'href="{name}"' in index for name in WIDGET_FILES)
            and index.count('data-testid="widget-link"') == len(WIDGET_FILES),
            f"links={index.count('data-testid="widget-link"')}",
        )
        pages = {name: raw(base, f"/{name}")["body"] for name in WIDGET_FILES}
        check(
            "every widget page links back to the index",
            all('href="index.html"' in body for body in pages.values())
            and all('data-testid="back-to-index"' in body for body in pages.values()),
        )
        check(
            "no served page leaks a template token or an HTML comment",
            not [
                name
                for name, body in pages.items()
                if "{{" in body or "<!--" in body or "{{SWITCH" in body
            ],
        )

        # --- 3. the golden page's DOM promises ---------------------------
        compose = pages["gmail-compose.html"]
        field = re.search(r'<input id="([^"]+)" data-testid="to"[^>]*>', compose)
        tag = field.group(0) if field else ""
        check(
            "the To field is a labelled role=combobox with aria-autocomplete=list",
            field is not None
            and 'role="combobox"' in tag
            and 'aria-autocomplete="list"' in tag
            and 'aria-label="To"' in tag
            and 'autocomplete="off"' in tag
            and re.search(r'<label class="field-label" for="[^"]+">To</label>', compose)
            is not None,
            f"tag={tag!r}",
        )
        controls = re.search(r'aria-controls="([^"]+)"', tag)
        listbox_id = re.search(r'<div id="([^"]+)" role="listbox"', compose)
        check(
            "the combobox's aria-controls points at the suggestion listbox in the same document",
            controls is not None
            and listbox_id is not None
            and controls.group(1) == listbox_id.group(1)
            and f'<div id="{controls.group(1)}" role="listbox" aria-label="Suggestions" hidden>'
            in compose,
            f"controls={controls.group(1) if controls else None}",
        )
        check(
            "the suggestion listbox is a labelled, empty role=listbox",
            'role="listbox" aria-label="Suggestions" hidden></div>' in compose,
        )
        check(
            "the chips container is a labelled role=listbox with a stable data-testid",
            'role="listbox" aria-label="Selected recipients" data-testid="recipient-chips"'
            in compose,
        )
        editor = re.search(
            r'<div contenteditable="true" role="textbox" aria-multiline="true" '
            r'aria-label="Message body"[^>]*data-testid="body-editor"',
            compose,
        )
        check(
            "the body editor is a contenteditable multiline textbox named Message body",
            editor is not None and 'aria-label="Message body"' in compose,
        )
        check(
            "the Compose and Send controls are real buttons with stable data-testids",
            '<button type="button" data-testid="compose">Compose</button>' in compose
            and '<button type="button" data-testid="send">Send</button>' in compose,
        )
        check(
            "the toast markup is a role=status with the undo testid, hidden until Send",
            '<div class="toast" role="status" data-testid="undo-toast" hidden></div>' in compose,
        )
        check(
            "the body and the chips are mirrored into hidden inputs",
            '<input type="hidden" name="body" value="">' in compose
            and '<input type="hidden" name="to" value="">' in compose,
        )

        # --- 4. the served script, not the markup ------------------------
        compose_js = raw(base, "/compose-app.js")["body"]
        editors_js = raw(base, "/editors.js")["body"]
        auto_js = raw(base, "/autocomplete.js")["body"]
        shadow_js = raw(base, "/shadow.js")["body"]
        check(
            "compose-app.js writes the body editor's textContent directly, from keydown",
            'messageBody.addEventListener("keydown", function (event) {' in compose_js
            and "messageBody.textContent = bodyText;" in compose_js
            and "bodyInput.value = bodyText;" in compose_js,
        )
        check(
            (
                "compose-app.js syncs the body editor from an input event (a replay's insertText) "
                "and dispatches nothing itself"
            ),
            'messageBody.addEventListener("input"' in compose_js
            and "bodyText = messageBody.textContent ||" in compose_js
            and "dispatchEvent" not in compose_js,
        )
        check(
            "compose-app.js clears the field and adds the chip in the same synchronous tick",
            'addChip(entry);\n    toField.value = "";' in compose_js
            and 'chip.setAttribute("data-address", entry.address);' in compose_js,
        )
        check(
            'compose-app.js names each chip\'s control "Remove <address>"',
            'remove.setAttribute("aria-label", "Remove " + entry.address);' in compose_js
            and 'remove.setAttribute("type", "button");' in compose_js,
        )
        check(
            "compose-app.js accepts on Enter or Tab while an option is highlighted",
            'event.key === "Enter" || event.key === "Tab"' in compose_js
            and "accept(suggests[highlighted]);" in compose_js,
        )
        check(
            "compose-app.js posts the send and shows the toast for four seconds",
            'window.Widgets.postJSON("/send", payload)' in compose_js
            and 'toast.textContent = "Sent \\u2014 Undo";' in compose_js
            and "}, 4000);" in compose_js,
        )
        check(
            "compose-app.js honours the no_toast switch before it shows anything",
            "switches().no_toast" in compose_js,
        )
        check(
            "editors.js mirrors a plain contenteditable from its own input event",
            'editor.addEventListener("input", function () {' in editors_js
            and 'store(editor.textContent || "");' in editors_js,
        )
        check(
            "the framework branch writes textContent from keydown, a direct DOM write",
            'editor.addEventListener("keydown", function (event) {' in editors_js
            and "editor.textContent = model;" in editors_js
            and "dispatchEvent" not in editors_js
            and 'data-mode="framework"' in pages["framework-editor.html"],
        )
        check(
            "editors.js re-renders the framework editor from the page's own state on a timer",
            "window.setInterval(function () {" in editors_js
            and "editor.textContent = model;" in editors_js,
        )
        check(
            "autocomplete.js accepts only on a click: Enter and Tab are inert",
            'event.key === "Enter" || event.key === "Tab"' in auto_js
            and "loadSuggestions(field.value.trim());" in auto_js
            and 'field.value = "";' in auto_js
            and "dispatchEvent" not in auto_js,
        )
        check(
            "the plain and framework editors and the autocomplete each carry one stable handle",
            'data-testid="editor"' in pages["plain-editor.html"]
            and 'data-testid="editor"' in pages["framework-editor.html"]
            and 'data-testid="reviewer"' in pages["react-autocomplete.html"]
            and 'data-testid="reviewer-chips"' in pages["react-autocomplete.html"]
            and 'data-source="plain-editor"' in pages["plain-editor.html"]
            and 'data-field="draft"' in pages["framework-editor.html"],
        )

        # --- 5. the shadow page ------------------------------------------
        check(
            "shadow.js attaches a real OPEN shadow root to the host element",
            'host.attachShadow({ mode: "open" })' in shadow_js
            and 'data-testid="shadow-host"' in pages["shadow-form.html"],
        )
        check(
            "the shadow root's markup is a labelled text input, a native select and Save",
            '<label for="shadow-name">Name</label>' in shadow_js
            and "<select" in shadow_js
            and "shadow-save" in shadow_js
            and "shadow-name" in shadow_js,
        )
        check(
            "shadow.js saves both fields through the shared store helper",
            'window.Widgets.save("shadow-form", { name: name.value, team: team.value })'
            in shadow_js,
        )

        # --- 6. the iframe page ------------------------------------------
        frame_page = pages["iframe-form.html"]
        check(
            "iframe-form.html embeds a same-origin frame served by this fixture",
            'data-testid="frame"' in frame_page
            and 'src="/iframe-child.html"' in frame_page
            and "cross-origin" not in frame_page,
        )
        frame = raw(base, "/iframe-child.html")
        check(
            "the framed document has a labelled text input and a Save button",
            frame["status"] == 200
            and '<label for="frame-note">Note</label>' in frame["body"]
            and 'data-testid="frame-save"' in frame["body"],
        )
        check(
            "the framed Save goes through the parent page's store",
            'window.parent.Widgets.save("iframe-form", {note: value});' in frame["body"],
        )

        # --- 7. the chip-removal page ------------------------------------
        removal = pages["chip-removal.html"]
        check(
            "chip-removal.html has chips with no remove control of their own",
            'data-testid="chips"' in removal
            and removal.count('class="chip" role="option"') == 3
            and 'aria-label="Remove' not in removal
            and 'type="submit"' not in removal,
        )
        check(
            "the removal is a Delete-key handler and a page menu, not a named button",
            'event.key === "Delete" && selected' in removal
            and 'data-testid="remove-selected"' in removal
            and 'role="menuitem"' in removal,
        )
        check(
            "its compose sends on Ctrl+Enter by fetch, with no submit event at all",
            'event.ctrlKey && event.key === "Enter"' in removal
            and 'window.Widgets.postJSON("/send", {' in removal
            and 'source: "chip-removal"' in removal
            and 'addEventListener("submit"' not in removal,
        )

        # --- 8. the perf page --------------------------------------------
        perf = pages["perf.html"]
        check(
            "perf.html generates exactly 1000 labelled text inputs",
            "var FIELD_COUNT = 1000;" in perf
            and "index <= FIELD_COUNT; index += 1" in perf
            and 'input.setAttribute("type", "text");' in perf
            and "container.appendChild(fragment);" in perf,
        )
        check(
            "perf.html is one container, filled by script rather than by markup",
            '<div class="fields" id="fields" data-testid="fields"></div>' in perf,
        )

        # --- 9. the send and save APIs over HTTP --------------------------
        reset(base)
        sent = post_send(base, {"to": [MARY], "body": BODY, "source": "gmail-compose"})
        after_send = state(base)
        check(
            "POST /send stores the message and /__test__/state reports it",
            sent["status"] == 200
            and json_response(sent)["ok"] is True
            and after_send["sends"] == 1
            and after_send["send_posts"] == 1
            and after_send["messages"] == [{"to": [MARY], "body": BODY, "source": "gmail-compose"}],
            f"state={after_send.get('messages')}",
        )
        expected_keys = {
            "messages",
            "sends",
            "saves",
            "values",
            "switches",
            "directory_size",
            "widgets",
        }
        check(
            "the state payload carries messages, sends, values and the switches",
            expected_keys.issubset(after_send) and after_send["directory_size"] == len(DIRECTORY),
        )
        stranger = post_send(base, {"to": ["dana@example.com"], "body": BODY})
        check(
            "POST /send refuses a non-synthetic address and stores nothing",
            stranger["status"] == 400
            and "example.test" in json_response(stranger)["error"]
            and state(base)["sends"] == 1
            and state(base)["rejected"] == 1,
        )
        saved = post_save(base, {"source": "plain-editor", "fields": {"note": "Kept"}})
        check(
            "POST /save stores a widget's value under its own source",
            saved["status"] == 200
            and json_response(saved)["values"] == {"note": "Kept"}
            and state(base)["values"]["plain-editor"]["note"] == "Kept"
            and state(base)["saves"] == 1
            and state(base)["save_posts"] == 1,
        )
        check(
            "POST /save refuses a payload without a source or fields",
            post_save(base, {"fields": {"note": "x"}})["status"] == 400
            and post_save(base, {"source": "plain-editor"})["status"] == 400
            and post_save(base, "not json")["status"] == 400,
        )

        # --- 10. the directory -------------------------------------------
        listing = json_response(raw(base, "/directory.json"))
        check(
            "GET /directory.json lists the whole synthetic directory",
            listing["count"] == len(DIRECTORY)
            and all(entry["address"].endswith("@example.test") for entry in listing["results"]),
        )
        filtered = json_response(raw(base, "/directory.json?q=ma"))
        check(
            "a typed prefix narrows the list to one suggestion, so a fold is unambiguous",
            filtered["query"] == "ma"
            and [entry["address"] for entry in filtered["results"]] == [MARY],
            f"results={filtered['results']}",
        )

        # --- 11. the switches --------------------------------------------
        reset(base)
        check(
            "slow_suggest_ms is off by default",
            server.DEFAULT_SWITCHES["slow_suggest_ms"] == 0
            and raw(base, "/directory.json?q=ma")["status"] == 200,
        )
        set_switches(base, slow_suggest_ms=400)
        started = time.time()
        slow = raw(base, "/directory.json?q=ma")
        elapsed = time.time() - started
        check(
            "slow_suggest_ms delays the suggestions, so a fast typist can race them",
            slow["status"] == 200
            and elapsed >= 0.35
            and json_response(slow)["count"] == 1
            and state(base)["switches"]["slow_suggest_ms"] == 400,
            f"elapsed={elapsed:.2f}",
        )
        reset(base)
        off_page = raw(base, "/gmail-compose.html")["body"]
        set_switches(base, no_toast=True)
        on_page = raw(base, "/gmail-compose.html")["body"]
        check(
            "no_toast is off by default and reaches the page when it is turned on",
            server.DEFAULT_SWITCHES["no_toast"] is False
            and '"no_toast": false' in off_page
            and '"no_toast": true' in on_page
            and state(base)["switches"]["no_toast"] is True,
        )
        check(
            "the switch that suppresses the toast is the page script's own check",
            "switches().no_toast" in compose_js and '"no_toast": true' in on_page,
        )
        set_switches(base, slow_send_ms=400)
        started = time.time()
        delayed = post_send(base, {"to": [MARY], "body": "late"})
        elapsed = time.time() - started
        check(
            "slow_send_ms delays the send and still stores the message",
            delayed["status"] == 200 and elapsed >= 0.35 and state(base)["sends"] == 1,
            f"elapsed={elapsed:.2f}",
        )
        set_switches(base, suggest_empty=True)
        empty = json_response(raw(base, "/directory.json?q=ma"))
        check(
            "suggest_empty leaves typing with no suggestions at all",
            empty["count"] == 0 and empty["results"] == [],
        )
        bad_delay = set_switches(base, slow_suggest_ms="soon")
        bad_flag = set_switches(base, no_toast=1)
        check(
            "a bad switch patch is refused with 400",
            bad_delay["status"] == 400 and bad_flag["status"] == 400,
        )
        reset(base)
        cleared = state(base)
        check(
            "POST /__test__/reset empties the store and clears the switches",
            cleared["messages"] == []
            and cleared["values"] == {}
            and cleared["sends"] == 0
            and cleared["saves"] == 0
            and cleared["switches"] == server.DEFAULT_SWITCHES
            and cleared["send_posts"] == 0
            and cleared["suggest_requests"] == 0
            and cleared["rejected"] == 0,
            f"state={cleared}",
        )

        # --- 12. nothing but synthetic data ------------------------------
        bodies = dict(pages)
        bodies["index"] = index
        bodies["frame"] = frame["body"]
        bodies.update({asset: raw(base, asset)["body"] for asset in ASSETS})
        bodies["directory"] = raw(base, "/directory.json")["body"]
        strangers = sorted(
            {
                address
                for body in bodies.values()
                for address in EMAIL_RE.findall(body)
                if not address.endswith("@example.test")
            }
        )
        check(
            "every address served anywhere is @example.test-synthetic",
            not strangers,
            f"strangers={strangers}",
        )
        check(
            "the directory the fixture offers is synthetic too",
            bool(DIRECTORY)
            and all(is_synthetic(entry["address"]) for entry in DIRECTORY)
            and len({entry["address"] for entry in DIRECTORY}) == len(DIRECTORY),
        )

        # --- 13. the two compile refusals (R1, R2) -----------------------
        CompileOptions, CompileError, compile_recording, reason = load_compiler()
        check(
            "packages.compiler.compile_recording imports for the refusal checks",
            compile_recording is not None,
            reason,
        )
        if compile_recording is not None:
            page_url = f"{base}/chip-removal.html"

            def options() -> object:
                return CompileOptions(
                    name="Widget corpus refusal",
                    when_to_use="checked by the corpus selftest",
                    sample_row={"to": MARY},
                    input_fields={"to": {"type": "string"}},
                    record_key="{{row.to | lower}}",
                    include_navigate=False,
                )

            removal_recording = [
                {
                    "kind": "fill",
                    "seq": 1,
                    "url": page_url,
                    "step": {
                        "id": "step-1",
                        "action": "fill",
                        "targets": [{"role": "combobox", "name": "To"}],
                        "value": {"kind": "constant", "value": MARY},
                        "clear_first": True,
                    },
                },
                {
                    "kind": "unrecordable",
                    "seq": 2,
                    "url": page_url,
                    "step": None,
                    "reason": "chip-removal",
                },
            ]
            ok, detail = compile_and_expect_refusal(
                compile_recording, CompileError, options(), removal_recording
            )
            check(
                "R2: a chip removed with no remove control refuses the compile",
                ok and CHIP_REFUSAL in detail,
                detail or "no problem text",
            )

            key_recording = [
                {
                    "kind": "fill",
                    "seq": 1,
                    "url": page_url,
                    "step": {
                        "id": "step-1",
                        "action": "fill",
                        "targets": [{"role": "textbox", "name": "Message"}],
                        "value": {"kind": "constant", "value": BODY},
                        "clear_first": True,
                    },
                },
                {
                    "kind": "press",
                    "seq": 2,
                    "url": page_url,
                    "step": {
                        "id": "step-2",
                        "action": "press",
                        "targets": [{"role": "textbox", "name": "Message"}],
                        "key": "Control+Enter",
                    },
                },
            ]
            ok, detail = compile_and_expect_refusal(
                compile_recording, CompileError, options(), key_recording
            )
            check(
                "R1: a recording that ends with a Ctrl+Enter send refuses the compile",
                ok and KEY_REFUSAL in detail,
                detail or "no problem text",
            )
    finally:
        fixture.shutdown()
        fixture.server_close()

    print("")
    if FAILURES:
        print(f"SELFTEST FAILED ({len(FAILURES)} of {CHECKS} checks failed): {', '.join(FAILURES)}")
        return 1
    print(f"SELFTEST PASSED ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
