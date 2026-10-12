#!/usr/bin/env python
"""Standalone proof that the messy-crm fixture works.

Run it directly (not through pytest)::

    .\\.venv\\Scripts\\python.exe fixtures\\sites\\messy-crm\\selftest.py

It starts the server in-process, exercises every route, the ``/__test__``
control API and every switch, prints one line per check, then
``SELFTEST PASSED`` or ``SELFTEST FAILED`` and exits 0 or 1.

The checks are the point of the fixture, so they are explicit about it: the
generated ids must differ between two loads of the same page while the stable
handles stay identical, every chip's ✕ must be a ``<button type="button">``
named ``Remove <address>`` and nested in the chip, the notes box must be a
labelled ``contenteditable``, the save must be a full-page POST that answers a
redirect and lands on a page whose markup already says ``Saved``, and every
address served anywhere must be ``@example.test``-synthetic.
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
from urllib.parse import urlencode, urlparse

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import server  # noqa: E402
from store import (  # noqa: E402
    DIRECTORY,
    STATIC_CHIP,
    MessyStore,
    is_synthetic,
    parse_recipients,
    search_directory,
)

FAILURES: list[str] = []
CHECKS = 0
ALICE = STATIC_CHIP
BOB = "bob@example.test"
CAROL = "carol@example.test"
NOTE = "Call alice about the quote"
SECOND_NOTE = "Second note, typed into the contenteditable"

#: Every page route, so the loop checks below cover all of them.
PAGE_ROUTES = (
    "/",
    "/recipients.html",
    "/notes.html",
    "/saved.html",
    "/second-page.html",
)

#: The three shapes of generated id a recorder has to drop.
REACT_ID_RE = re.compile(r":r\d+[a-z]:")
GMAIL_ID_RE = re.compile(r":[0-9a-z]{2}")
BLOB_ID_RE = re.compile(r"[0-9a-f]{16}")
GENERATED_ID_RE = re.compile(r'id="(:[^"]+|[0-9a-f]{16})"')

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")
CHIP_SPLIT = 'class="chip" role="option"'


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
    """One request, no redirect following, so 303 is observable."""
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


def form(**fields) -> str:
    return urlencode({k: v for k, v in fields.items()})


def state(crm: str) -> dict:
    return json_response(raw(crm, "/__test__/state"))


def reset(crm: str) -> dict:
    return json_response(raw(crm, "/__test__/reset", "POST"))


def set_switches(crm: str, **patch):
    return raw(crm, "/__test__/switches", "POST", json.dumps(patch), "application/json")


def save(crm: str, **fields):
    """POST the save form the way the browser would: urlencoded, full page."""
    return raw(crm, "/save", "POST", form(**fields), "application/x-www-form-urlencoded")


def chip_chunks(body: str) -> list[str]:
    """One chunk per chip: from the chip's own class attribute onwards."""
    tail = body.split('data-testid="recipient-chips"', 1)[-1]
    return [CHIP_SPLIT + part for part in tail.split(CHIP_SPLIT)[1:]]


def handles(body: str) -> tuple:
    """The stable handles: identical on every load, whatever the ids do."""
    return (
        sorted(re.findall(r'data-testid="[^"]+"', body)),
        sorted(re.findall(r'name="[^"]+"', body)),
        sorted(re.findall(r'aria-label="[^"]+"', body)),
        sorted(re.findall(r'<label for="[^"]+">([^<]+)</label>', body)),
    )


def main() -> int:
    # The check names are ASCII, but a failure detail quotes page markup, which
    # carries the ✕ glyph. A cp1252 console must not turn a FAIL into a crash.
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
    handshake = None
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
    store = MessyStore()
    check(
        "store.record stores a recipient and the notes",
        store.record([ALICE], NOTE) == {"recipients": [ALICE], "notes": NOTE},
    )
    check("store.last reads the newest record back", (store.last() or {}).get("notes") == NOTE)
    store.record([BOB, ALICE], SECOND_NOTE)
    check(
        "store.recipient_counts counts every saved address",
        store.recipient_counts() == {ALICE: 2, BOB: 1},
    )
    check(
        "store.notes lists every note in save order",
        store.notes() == [NOTE, SECOND_NOTE],
    )
    check(
        "store.counts reports the shape /__test__/state serves",
        store.counts()
        == {"saves": 2, "recipients": {ALICE: 2, BOB: 1}, "notes": [NOTE, SECOND_NOTE]},
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
        "search_directory matches a name or an address and caps the list",
        [entry["address"] for entry in search_directory("ali")] == [ALICE]
        and search_directory("") == []
        and len(search_directory("example.test")) == 5,
    )
    store.reset()
    check(
        "store.reset empties the store",
        store.counts() == {"saves": 0, "recipients": {}, "notes": []} and store.last() is None,
    )

    # --- start the fixture in-process -----------------------------------
    fixture = server.make_server(0)
    crm = f"http://127.0.0.1:{fixture.server_port}"
    threading.Thread(target=fixture.serve_forever, name="messy-crm", daemon=True).start()
    check(
        "make_server binds a loopback port",
        fixture.server_port > 0 and crm.startswith("http://127.0.0.1:"),
    )

    try:
        # --- 1. the compose page ---------------------------------------
        compose_response = raw(crm, "/")
        compose = compose_response["body"]
        check(
            "GET / is 200 UTF-8 HTML",
            compose_response["status"] == 200
            and compose_response["headers"].get("content-type") == "text/html; charset=utf-8",
        )
        check(
            "a fresh compose page shows no status at all",
            compose_response["status"] == 200 and 'role="status"' not in compose,
        )
        combo = re.search(r"<input [^>]*data-testid=\"recipient-input\"[^>]*>", compose)
        combo_tag = combo.group(0) if combo else ""
        check(
            "the recipient input is a role=combobox with a name, a label and a stable data-testid",
            combo is not None
            and 'role="combobox"' in combo_tag
            and 'name="to"' in combo_tag
            and 'aria-autocomplete="list"' in combo_tag
            and 'autocomplete="off"' in combo_tag
            and re.search(r"<label for=\"[^\"]+\">Add recipient</label>", compose) is not None,
            f"tag={combo_tag!r}",
        )
        combo_id = re.search(r"id=\"([^\"]+)\"", combo_tag)
        controls = re.search(r'aria-controls="([^"]+)"', combo_tag)
        suggestions_id = re.search(r'<div id="([^"]+)" class="suggestions"', compose)
        check(
            "the combobox points aria-controls at the suggestions listbox in the same document",
            combo_id is not None
            and controls is not None
            and suggestions_id is not None
            and controls.group(1) == suggestions_id.group(1)
            and f'id="{controls.group(1)}"' in compose,
        )
        check(
            "the suggestions listbox is an empty role=listbox with a stable data-testid",
            '<div id="' in compose
            and 'class="suggestions" role="listbox" aria-label="Recipient suggestions" ' in compose
            and 'data-testid="recipient-suggestions" hidden></div>' in compose,
        )
        check(
            "the Add action is a real button named Add with a stable data-testid",
            re.search(
                r'<button id="[^"]+" type="button" data-testid="recipient-add">Add</button>',
                compose,
            )
            is not None,
        )

        # --- 2. the pre-rendered chip and its ✕ -------------------------
        chunks = chip_chunks(compose)
        check(
            "the chips container is a role=listbox holding exactly one pre-rendered chip",
            'role="listbox" aria-label="Selected recipients" aria-multiselectable="false" '
            'data-testid="recipient-chips"'
            in compose
            and len(chunks) == 1,
            f"chips={len(chunks)}",
        )
        check(
            "the pre-rendered chip is alice@example.test, the address the file ships",
            len(chunks) == 1
            and f'data-recipient="{ALICE}"' in chunks[0]
            and f'<span class="chip-name">{ALICE}</span>' in chunks[0],
        )
        check(
            "the chip is itself clickable (role=option), which is the trap",
            'class="chip" role="option" aria-selected="false" data-testid="recipient-chip"'
            in compose
            and len(chunks) == 1,
        )
        check(
            'the alice chip nests <button type="button" aria-label="Remove alice@example.test">',
            '<button type="button" aria-label="Remove alice@example.test">' in compose
            and f'<button type="button" aria-label="Remove {ALICE}">✕</button>' in chunks[0],
            f"chunk={chunks[0][:200]!r}",
        )
        pairs = []
        for chunk in chunks:
            address = re.search(r'data-recipient="([^"]+)"', chunk)
            button = re.search(r'<button type="button" aria-label="Remove ([^"]+)">', chunk)
            pairs.append((address.group(1) if address else "", button.group(1) if button else ""))
        check(
            'every chip\'s close button carries aria-label="Remove <name>"',
            bool(pairs) and all(address and name == address for address, name in pairs),
            f"pairs={pairs}",
        )
        check(
            "the close button is a type=button, never a submit, and needs no id to be reachable",
            re.search(r'<button type="button" aria-label="Remove [^"]+">✕</button>', compose)
            is not None
            and re.search(r'<button[^>]*id="[^"]*"[^>]*aria-label="Remove', compose) is None,
        )

        # --- 3. the served markup is static, not injected ---------------
        static_recipients = (HERE / "public" / "recipients.html").read_text(encoding="utf-8")
        static_index = (HERE / "public" / "index.html").read_text(encoding="utf-8")
        check(
            "both served pages carry the alice chip as static markup in the file",
            all(
                f'data-recipient="{ALICE}"' in text
                and 'class="chip" role="option"' in text
                and f'<button type="button" aria-label="Remove {ALICE}">✕</button>' in text
                for text in (static_recipients, static_index)
            ),
        )
        check(
            "the close button carries nothing but type and its own name: no id, no testid",
            re.search(
                r'<button type="button" aria-label="Remove [^"]+">✕</button>', static_recipients
            )
            is not None
            and 'data-testid="chip-remove"' not in static_recipients
            and 'button[aria-label^="Remove"]' not in static_recipients,
        )

        # --- 4. fresh ids on every load, stable handles in between ------
        first = raw(crm, "/")
        second = raw(crm, "/")
        ids_first = set(GENERATED_ID_RE.findall(first["body"]))
        ids_second = set(GENERATED_ID_RE.findall(second["body"]))
        check(
            "two loads of the same page mint different generated ids",
            bool(ids_first) and bool(ids_second) and not (ids_first & ids_second),
            f"{sorted(ids_first)} vs {sorted(ids_second)}",
        )
        check(
            "the generated ids have the three shapes a recorder has to drop",
            any(REACT_ID_RE.fullmatch(value) for value in ids_first)
            and any(GMAIL_ID_RE.fullmatch(value) for value in ids_first)
            and any(BLOB_ID_RE.fullmatch(value) for value in ids_first),
            f"ids={sorted(ids_first)}",
        )
        check(
            "the stable handles are identical between the two loads",
            handles(first["body"]) == handles(second["body"])
            and 'data-testid="recipient-input"' in first["body"]
            and 'data-testid="save"' in first["body"]
            and 'data-testid="notes-box"' in first["body"]
            and f'aria-label="Remove {ALICE}"' in first["body"],
        )

        # --- 5. the notes box ------------------------------------------
        check(
            "the notes box is a div contenteditable, labelled, with a stable data-testid",
            re.search(r'<div [^>]*contenteditable="true"[^>]*>', compose) is not None
            and 'data-testid="notes-box"' in compose
            and 'aria-labelledby="notes-label"' in compose
            and 'aria-label="Notes"' in compose
            and '<span class="field-label" id="notes-label">Notes</span>' in compose,
        )
        check(
            "the notes box is a role=textbox with a multiline hint",
            'role="textbox"' in compose and 'aria-multiline="true"' in compose,
        )
        check(
            "a fresh notes box is empty and posts through a hidden input of its own",
            re.search(r'<div [^>]*data-testid="notes-box"[^>]*></div>', compose) is not None
            and re.search(r'<input id="[^"]+" type="hidden" name="notes" value="">', compose)
            is not None,
        )

        # --- 6. the save is a full-page POST ----------------------------
        check(
            "the save is a plain form POST to /save, never a fetch",
            re.search(
                r'<form id="[^"]+" action="/save" method="post" data-testid="save-form">', compose
            )
            is not None
            and 'enctype="multipart/form-data"' not in compose
            and re.search(
                r'<button id="[^"]+" type="submit" data-testid="save">Save</button>', compose
            )
            is not None,
        )
        check(
            "the form carries the recipients and the notes as hidden inputs",
            'type="hidden" name="recipients"' in compose
            and 'type="hidden" name="notes"' in compose,
        )
        check(
            "the served markup already carries the pre-rendered chip's address as the form value",
            f'name="recipients" value="{ALICE}"' in compose,
        )

        # --- 7. the autocomplete data -----------------------------------
        listing_response = raw(crm, "/recipients.json")
        listing = json_response(listing_response)
        check(
            "GET /recipients.json lists the whole synthetic directory",
            listing_response["status"] == 200
            and listing["count"] == len(DIRECTORY)
            and [entry["address"] for entry in listing["results"]]
            == [entry["address"] for entry in DIRECTORY]
            and all(entry["address"].endswith("@example.test") for entry in listing["results"]),
        )
        filtered = json_response(raw(crm, "/recipients.json?q=ali"))
        check(
            "GET /recipients.json?q= filters on the name and on the address",
            filtered["query"] == "ali"
            and [entry["address"] for entry in filtered["results"]] == [ALICE],
        )
        blank = json_response(raw(crm, "/recipients.json?q="))
        check(
            "an empty query matches nothing, like a search box",
            blank["count"] == 0 and blank["results"] == [],
        )

        # --- 8. the saved page before any save --------------------------
        empty_saved = raw(crm, "/saved.html")
        check(
            "saved.html renders the status on load even before a save, and says nothing landed yet",
            empty_saved["status"] == 200
            and '<div role="status">Saved</div>' in empty_saved["body"]
            and 'data-testid="nothing-saved"' in empty_saved["body"]
            and state(crm)["saves"] == 0,
        )

        # --- 9. a real save ---------------------------------------------
        reset(crm)
        saved = save(crm, recipients=f"{ALICE},{BOB}", notes=NOTE, page="compose")
        check(
            "POST /save answers 303 to /saved.html: a navigation, not JSON and not a rendered form",
            saved["status"] == 303
            and saved["headers"].get("location") == "/saved.html"
            and saved["body"] == "",
        )
        saved_page = raw(crm, "/saved.html")
        check(
            'the saved page has <div role="status">Saved</div> in its markup on load',
            saved_page["status"] == 200 and '<div role="status">Saved</div>' in saved_page["body"],
        )
        check(
            "the saved page reads the record back from the store",
            f'data-testid="saved-recipients">{ALICE}, {BOB}<' in saved_page["body"]
            and f'data-testid="saved-notes">{NOTE}<' in saved_page["body"]
            and 'data-testid="saved-count">1<' in saved_page["body"],
        )
        after_save = state(crm)
        check(
            "the store recorded the recipient and the notes",
            after_save["recipients"] == {ALICE: 1, BOB: 1}
            and after_save["notes"] == [NOTE]
            and after_save["saves"] == 1
            and after_save["created"] == 1
            and after_save["save_posts"] == 1,
            f"state={ {k: after_save[k] for k in ('saves', 'recipients', 'notes')} }",
        )
        check(
            "the saved page links back to the compose page",
            'data-testid="back-to-compose" href="index.html">Back to compose</a>'
            in saved_page["body"],
        )
        typed = save(crm, recipients=CAROL, notes="Kept for the second save", page="compose")
        check(
            "a second save replaces what the saved page shows",
            typed["status"] == 303
            and 'data-testid="saved-notes">Kept for the second save<'
            in raw(crm, "/saved.html")["body"],
        )

        # --- 10. a refused save -----------------------------------------
        reset(crm)
        stranger = save(crm, recipients="someone@real-company.example", notes="", page="compose")
        check(
            "a non-synthetic recipient is refused with role=alert and stores nothing",
            stranger["status"] == 200
            and '<div role="alert" class="field-error">' in stranger["body"]
            and "must be @example.test" in stranger["body"]
            and state(crm)["saves"] == 0
            and state(crm)["rejected"] == 1,
        )
        reset(crm)
        from_notes = save(
            crm, recipients="someone@real-company.example", notes="Keep me", page="notes"
        )
        check(
            "a refused save re-renders the page its form came from, with what was typed",
            from_notes["status"] == 200
            and "<title>Notes</title>" in from_notes["body"]
            and 'role="alert"' in from_notes["body"]
            and "Keep me" in from_notes["body"]
            and state(crm)["saves"] == 0,
        )
        reset(crm)
        notes_only = save(crm, recipients="", notes=SECOND_NOTE, page="notes")
        check(
            "the notes page posts no recipients and the store still records the note",
            notes_only["status"] == 303
            and state(crm)["notes"] == [SECOND_NOTE]
            and state(crm)["recipients"] == {},
        )

        # --- 11. the second page and the link back ----------------------
        second_page = raw(crm, "/second-page.html")
        check(
            "the second page is reachable",
            second_page["status"] == 200 and "<h1>Second page</h1>" in second_page["body"],
        )
        check(
            "the second page links back to the compose page",
            re.search(r'<a[^>]*href="index\.html">Back to compose</a>', second_page["body"])
            is not None,
        )
        check(
            "the compose page links to the second page",
            re.search(r'<a[^>]*href="second-page\.html">Second page</a>', compose) is not None,
        )
        check(
            "the recipients page and the notes page both link back too, so no page is a dead end",
            re.search(
                r'<a[^>]*href="index\.html">Back to compose</a>',
                raw(crm, "/recipients.html")["body"],
            )
            is not None
            and re.search(
                r'<a[^>]*href="index\.html">Back to compose</a>', raw(crm, "/notes.html")["body"]
            )
            is not None,
        )
        check(
            "every short route answers 200 as well",
            all(
                raw(crm, route)["status"] == 200
                for route in ("/index.html", "/recipients", "/notes", "/saved", "/second-page")
            ),
        )

        # --- 12. the recipients and notes pages on their own ------------
        recipients_page = raw(crm, "/recipients.html")
        notes_page = raw(crm, "/notes.html")
        check(
            "the recipients page carries the same composer, chip and close-button shape",
            recipients_page["status"] == 200
            and f'<button type="button" aria-label="Remove {ALICE}">✕</button>'
            in recipients_page["body"]
            and len(chip_chunks(recipients_page["body"])) == 1
            and 'data-testid="recipient-input"' in recipients_page["body"]
            and 'action="/save" method="post"' in recipients_page["body"],
        )
        check(
            "the notes page carries the contenteditable and a save form of its own",
            notes_page["status"] == 200
            and re.search(r'<div [^>]*data-testid="notes-box"[^>]*>', notes_page["body"])
            is not None
            and 'action="/save" method="post"' in notes_page["body"]
            and 'name="recipients"' not in notes_page["body"],
        )

        # --- 13. the switches -------------------------------------------
        reset(crm)
        set_switches(crm, slow_save_ms=400)
        started = time.time()
        slow = save(crm, recipients=f"{ALICE}", notes="late", page="compose")
        elapsed = time.time() - started
        check(
            "slow_save_ms delays the save and still redirects to the saved page",
            slow["status"] == 303 and elapsed >= 0.35 and state(crm)["saves"] == 1,
            f"elapsed={elapsed:.2f}",
        )

        reset(crm)
        set_switches(crm, slow_suggest_ms=400)
        started = time.time()
        slow_suggest = raw(crm, "/recipients.json?q=ali")
        suggest_elapsed = time.time() - started
        check(
            "slow_suggest_ms delays the suggestions, so a fast typist can race them",
            slow_suggest["status"] == 200
            and suggest_elapsed >= 0.35
            and json_response(slow_suggest)["count"] == 1,
            f"elapsed={suggest_elapsed:.2f}",
        )

        reset(crm)
        set_switches(crm, stale_status=True)
        stale = raw(crm, "/")["body"]
        check(
            "stale_status renders the real status markup on a page that saved nothing",
            '<div role="status">Saved</div>' in stale and state(crm)["saves"] == 0,
        )
        check(
            "the stale status is byte-identical to the status the saved page serves",
            stale.count('<div role="status">Saved</div>') == 1
            and '<div role="status">Saved</div>' in raw(crm, "/saved.html")["body"],
        )

        reset(crm)
        set_switches(crm, reject_recipients=True)
        rejected = save(crm, recipients=f"{ALICE}", notes="", page="compose")
        check(
            "reject_recipients refuses every save without storing",
            rejected["status"] == 200
            and "that recipient is not in the directory" in rejected["body"]
            and state(crm)["saves"] == 0
            and state(crm)["rejected"] == 1,
        )

        reset(crm)
        set_switches(crm, suggest_empty=True)
        empty_suggest = json_response(raw(crm, "/recipients.json?q=ali"))
        check(
            "suggest_empty leaves typing with no suggestions at all",
            empty_suggest["count"] == 0 and empty_suggest["results"] == [],
        )

        bad = raw(crm, "/__test__/switches", "POST", '{"slow_save_ms": "soon"}', "application/json")
        check("POST /__test__/switches rejects a bad delay", bad["status"] == 400)
        reset(crm)
        cleared = state(crm)
        check(
            "POST /__test__/reset empties the store and clears the switches",
            cleared["saves"] == 0
            and cleared["recipients"] == {}
            and cleared["notes"] == []
            and cleared["switches"] == server.DEFAULT_SWITCHES
            and cleared["save_posts"] == 0
            and cleared["rejected"] == 0,
        )
        check(
            "GET /__test__/state reports the fixture's own truth",
            set(["saves", "recipients", "notes", "switches", "static_chip", "renders"]).issubset(
                cleared
            )
            and cleared["static_chip"] == ALICE
            and cleared["directory_size"] == len(DIRECTORY)
            and cleared["renders"] == 0,
        )
        raw(crm, "/")
        moved = state(crm)
        check(
            "the render counter moves with one page load, so ids can be seen to move too",
            moved["renders"] == 1 and moved["saves"] == 0 and moved["save_posts"] == 0,
        )

        # --- 14. static assets and 404s ---------------------------------
        app_js_response = raw(crm, "/app.js")
        styles_response = raw(crm, "/styles.css")
        app_js = app_js_response["body"]
        styles = styles_response["body"]
        check(
            "app.js and styles.css are served with the right types",
            app_js_response["headers"].get("content-type") == "text/javascript; charset=utf-8"
            and styles_response["headers"].get("content-type") == "text/css; charset=utf-8",
        )
        check(
            "app.js asks the fixture itself for the suggestions",
            'fetch("/recipients.json?q="' in app_js and "encodeURIComponent" in app_js,
        )
        check(
            "app.js mints a fresh id for every option it renders",
            'option.setAttribute("id", mintId())' in app_js and '":r" +' in app_js,
        )
        check(
            "app.js builds the close button named Remove <address>, nested in the chip",
            'remove.setAttribute("type", "button")' in app_js
            and 'remove.setAttribute("aria-label", "Remove " + value)' in app_js
            and "chip.appendChild(remove)" in app_js
            and 'chip.setAttribute("data-testid", "recipient-chip")' in app_js,
        )
        check(
            "app.js mirrors the notes box and the chips into the hidden inputs",
            'input[name="notes"]' in app_js
            and 'input[name="recipients"]' in app_js
            and "syncNotes" in app_js
            and "syncRecipients" in app_js,
        )
        check(
            "app.js never posts the save itself, so the browser really navigates",
            '"/save"' not in app_js and "XMLHttpRequest" not in app_js,
        )
        check(
            "app.js marks the stale status when the switch is on",
            "stale_status" in app_js and "data-stale-status" in app_js,
        )
        check(
            "the chips and the close button get real boxes in styles.css",
            ".chip button" in styles
            and ".chips" in styles
            and ".notes" in styles
            and "min-height" in styles,
        )
        check(
            "unknown routes answer 404 (GET and POST)",
            raw(crm, "/nope")["status"] == 404
            and raw(crm, "/nope", "POST")["status"] == 404
            and raw(crm, "/save")["status"] == 404,
        )
        check("favicon.ico answers 204", raw(crm, "/favicon.ico")["status"] == 204)

        # --- 15. no leaks, and nothing but synthetic data ---------------
        body_by_route = {route: raw(crm, route)["body"] for route in PAGE_ROUTES}
        leaks = [
            route
            for route, body in body_by_route.items()
            if "{{" in body or "<!--" in body or ":r0" in body
        ]
        check(
            "no served page leaks a template token or an HTML comment", not leaks, f"leaks={leaks}"
        )
        strangers = sorted(
            {
                address
                for body in list(body_by_route.values()) + [app_js]
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
            and all(entry["address"].endswith("@example.test") for entry in DIRECTORY)
            and all(is_synthetic(entry["address"]) for entry in DIRECTORY),
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
