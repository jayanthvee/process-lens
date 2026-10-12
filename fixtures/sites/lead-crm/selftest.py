#!/usr/bin/env python
"""Standalone proof that the lead-crm fixture works.

Run it directly (not through pytest)::

    .\\.venv\\Scripts\\python.exe fixtures\\sites\\lead-crm\\selftest.py

It starts the server in-process, exercises every route, the ``/__test__``
control API and every switch, prints one line per check, then
``SELFTEST PASSED`` or ``SELFTEST FAILED`` and exits 0 or 1.
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
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import server  # noqa: E402
from store import LeadStore  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0
LEAD_EMAIL = SMTP_EMAIL = "ana@example.test"
SECOND_EMAIL = "bo@example.test"


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
    """One request, no redirect following, so 302 is observable."""
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
    from urllib.parse import urlencode

    return urlencode({k: v for k, v in fields.items()})


def set_switches(crm: str, **patch):
    return raw(crm, "/__test__/switches", "POST", json.dumps(patch), "application/json")


def state(crm: str) -> dict:
    return json_response(raw(crm, "/__test__/state"))


def save(crm: str, email: str, interest: str = "Data course", company: str = "Acme Analytics"):
    return raw(
        crm,
        "/leads/new",
        "POST",
        form(email=email, interest=interest, company=company),
        "application/x-www-form-urlencoded",
    )


def reset(crm: str) -> dict:
    return json_response(raw(crm, "/__test__/reset", "POST"))


def main() -> int:
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
        and set(handshake) == {"crm_url", "partner_url", "crm_port", "partner_port"}
        and handshake["crm_url"] == f"http://127.0.0.1:{handshake['crm_port']}"
        and handshake["partner_url"] == f"http://127.0.0.1:{handshake['partner_port']}"
        and handshake["crm_port"] != handshake["partner_port"],
        f"line={line!r}",
    )

    # --- 0b. store unit checks ------------------------------------------
    store = LeadStore()
    check("store.create stores a lead", store.create("A", "Data course", "Acme") is True)
    check(
        "store.create is case-insensitive and refuses a duplicate",
        store.create("a", "Other", "Other") is False and store.count_for("A") == 1,
    )
    check(
        "store.search returns zero or one lead",
        [lead["email"] for lead in store.search("a@x.test")] == []
        and len(store.search(" a ")) == 1,
    )
    check("store.counts reports one per email", store.counts() == {"a": 1})
    store.reset()
    check("store.reset empties the store", store.counts() == {} and store.count_for("a") == 0)

    # --- start the fixture in-process -----------------------------------
    crm_server = server.make_server(0, 0)
    partner_server = crm_server.partner_server
    crm = f"http://127.0.0.1:{crm_server.server_port}"
    partner = f"http://127.0.0.1:{partner_server.server_port}"
    if crm_server.server_port == partner_server.server_port:
        check("CRM and partner are on different ports", False, f"{crm} == {partner}")
    for srv, name in ((crm_server, "crm"), (partner_server, "partner")):
        threading.Thread(target=srv.serve_forever, name=name, daemon=True).start()
    check(
        "make_server starts a CRM origin and a separate partner origin",
        crm != partner and crm_server.server_port > 0 and partner_server.server_port > 0,
    )

    try:
        # --- 1. index page ----------------------------------------------
        index = raw(crm, "/")
        body = index["body"]
        check(
            "GET / is 200 UTF-8 HTML",
            index["status"] == 200
            and index["headers"].get("content-type") == "text/html; charset=utf-8",
        )
        check(
            "search box is the briefed input",
            '<label for="lead-search">Search leads</label>' in body
            and 'id="lead-search"' in body
            and 'data-testid="lead-search"' in body,
        )
        check(
            "search box ladder: data-testid + label for + accessible name from the label",
            '<input id="lead-search" name="q"' in body
            and 'data-testid="lead-search"' in body
            and '<label for="lead-search">Search leads</label>' in body,
        )
        button = re.search(
            r"<button[^>]*data-testid=\"lead-search-button\"[^>]*>(.*?)</button>", body
        )
        check(
            "search button: role=button with accessible name Search",
            button is not None
            and button.group(1).strip() == "Search"
            and 'role="button"' in button.group(0),
        )
        check(
            "index links to the new-lead page",
            re.search(r'<a[^>]*href="new-lead\.html"[^>]*>New lead</a>', body) is not None,
        )
        check(
            "index links to the partner origin (off-origin target)",
            f'href="{partner}/partner.html"' in body and "Open partner site" in body,
        )
        check(
            "server injected the switches into the page",
            "window.__SWITCHES__" in body and "<!--SWITCHES-->" not in body and "{{Q}}" not in body,
        )

        # --- 2. search miss --------------------------------------------
        miss = raw(crm, "/?q=" + SMTP_EMAIL)
        check(
            "search miss renders No leads found and no rows",
            miss["status"] == 200
            and "No leads found" in miss["body"]
            and miss["body"].count("data-lead-row") == 0,
        )
        miss_json = raw(crm, "/search.json?q=" + SMTP_EMAIL)
        check(
            "GET /search.json on a miss is an empty JSON result",
            miss_json["status"] == 200
            and json_response(miss_json)["count"] == 0
            and json_response(miss_json)["results"] == [],
        )

        # --- 3. a successful save --------------------------------------
        created = save(crm, SMTP_EMAIL)
        check(
            "POST /leads/new stores the lead and renders the toast",
            created["status"] == 200
            and '<div role="status"' in created["body"]
            and "Lead created" in created["body"],
        )
        check(
            "a real save renders exactly the briefed toast markup",
            '<div role="status">Lead created</div>' in created["body"],
        )
        hit = raw(crm, "/?q=" + SMTP_EMAIL)
        check(
            "search hit renders exactly one [data-lead-row]",
            hit["body"].count("data-lead-row") == 1 and "No leads found" not in hit["body"],
        )
        check(
            "search hit is an ARIA table with the lead in a role=cell",
            'role="table"' in hit["body"]
            and 'aria-label="Search results"' in hit["body"]
            and f'<div role="cell" class="result-cell">{SMTP_EMAIL}</div>' in hit["body"],
        )
        check(
            "search is case-insensitive",
            raw(crm, "/?q=" + SMTP_EMAIL.upper())["body"].count("data-lead-row") == 1,
        )
        hit_json = json_response(raw(crm, "/search.json?q=" + SMTP_EMAIL))
        check(
            "GET /search.json on a hit returns the stored lead",
            hit_json["count"] == 1
            and hit_json["results"][0]
            == {"email": SMTP_EMAIL, "interest": "Data course", "company": "Acme Analytics"},
        )

        # --- 4. a duplicate save ---------------------------------------
        duplicate = save(crm, SMTP_EMAIL, interest="Something else", company="Other co")
        check(
            "duplicate save renders the field error and no toast",
            duplicate["status"] == 200
            and '<div role="alert" class="field-error">that email already exists</div>'
            in duplicate["body"]
            and "Lead created" not in duplicate["body"],
        )
        check(
            "a rejected form comes back with what was already typed",
            f'value="{SMTP_EMAIL}"' in duplicate["body"]
            and 'value="Other co"' in duplicate["body"],
        )
        after_duplicate = state(crm)
        check(
            "duplicate save stores nothing: leads still 1, saves 2",
            after_duplicate["leads"] == {SMTP_EMAIL: 1}
            and after_duplicate["saves"] == 2
            and after_duplicate["duplicates"] == 1,
        )

        # --- 5. the test API -------------------------------------------
        check(
            "GET /__test__/state reports leads, saves and the switches",
            set(["leads", "saves", "switches"]).issubset(after_duplicate)
            and after_duplicate["switches"] == server.DEFAULT_SWITCHES
            and after_duplicate["disconnect_pending"] is False,
        )
        marked = json_response(raw(crm, "/__test__/mark-save", "POST"))
        check(
            "POST /__test__/mark-save increments saves",
            marked["saves"] == 3 and state(crm)["mark_save_calls"] == 1,
        )
        bad = raw(crm, "/__test__/switches", "POST", '{"slow_save_ms": "soon"}', "application/json")
        check("POST /__test__/switches rejects a bad slow_save_ms", bad["status"] == 400)
        empty_query = raw(crm, "/?q=")
        check(
            "an empty query says No leads found while an unsearched page shows no region",
            "No leads found" in empty_query["body"]
            and "No leads found" not in raw(crm, "/")["body"]
            and "data-lead-row" not in empty_query["body"],
        )
        login_post = raw(
            crm, "/login.html", "POST", "password=x", "application/x-www-form-urlencoded"
        )
        check(
            "a stray POST /login.html renders the login page, not a 404",
            login_post["status"] == 200 and "<title>Sign in</title>" in login_post["body"],
        )

        # --- 6. the new-lead page --------------------------------------
        new_lead = raw(crm, "/new-lead.html")["body"]
        check(
            "new-lead fields are the briefed ones",
            '<label for="lead-email">Email</label>' in new_lead
            and 'id="lead-email"' in new_lead
            and 'data-testid="lead-email"' in new_lead
            and '<label for="lead-interest">Interest</label>' in new_lead
            and 'data-testid="lead-interest"' in new_lead
            and '<option value="">Interest</option>' in new_lead
            and '<option value="Data course">Data course</option>' in new_lead
            and '<label for="lead-company">Company</label>' in new_lead
            and 'data-testid="lead-company"' in new_lead,
        )
        save_button = re.search(
            r"<button[^>]*data-testid=\"lead-save\"[^>]*>(.*?)</button>", new_lead
        )
        check(
            "Save button carries data-testid=lead-save and the name exactly Save",
            save_button is not None and save_button.group(1).strip() == "Save",
        )
        check(
            "the hidden decoy listbox with two duplicate options is present",
            '<div role="listbox" hidden><div role="option">Data course</div>'
            '<div role="option">Data course</div></div>' in new_lead,
        )
        check(
            "the form posts to /leads/new with method=post (urlencoded by default)",
            'action="/leads/new"' in new_lead
            and 'method="post"' in new_lead
            and 'enctype="multipart/form-data"' not in new_lead,
        )
        check("no toast on a fresh new-lead page", 'role="status"' not in new_lead)

        # --- 7. the switches -------------------------------------------
        reset(crm)
        set_switches(crm, slow_save_ms=400)
        started = time.time()
        slow = save(crm, SMTP_EMAIL)
        elapsed = time.time() - started
        check(
            "slow_save_ms makes /leads/new slow but still saves",
            slow["status"] == 200
            and "Lead created" in slow["body"]
            and elapsed >= 0.35
            and state(crm)["leads"] == {SMTP_EMAIL: 1},
        )

        reset(crm)
        set_switches(crm, stale_toast=True)
        stale = raw(crm, "/new-lead.html")["body"]
        check(
            "stale_toast renders Lead created on load with no save",
            '<div role="status">Lead created</div>' in stale
            and state(crm)["leads"] == {},
        )

        reset(crm)
        set_switches(crm, login_page=True)
        login = save(crm, SMTP_EMAIL)
        check(
            "login_page answers 302 to /login.html and stores nothing",
            login["status"] == 302
            and login["headers"].get("location") == "/login.html"
            and state(crm)["leads"] == {},
        )
        login_page = raw(crm, "/login.html")
        styles = raw(crm, "/styles.css")
        check(
            "login.html has a visible password field and a Sign in button",
            "<title>Sign in</title>" in login_page["body"]
            and '<input id="password" name="password" type="password"' in login_page["body"]
            and ">Sign in</button>" in login_page["body"]
            and "hidden" not in login_page["body"].split('id="password"')[0].split("<input")[-1]
            and re.search(r"input,[\s\S]*?min-height: 36px", styles["body"]) is not None,
        )

        reset(crm)
        set_switches(crm, challenge_page=True)
        challenge = save(crm, SMTP_EMAIL)
        check(
            "challenge_page answers challenge.html with status 200 and stores nothing",
            challenge["status"] == 200
            and "<title>Just a moment</title>" in challenge["body"]
            and 'src="https://challenges.cloudflare.com/turnstile/v0/api.js"' in challenge["body"]
            and state(crm)["leads"] == {},
        )
        check(
            "challenge.html is reachable on its own and never depends on the URL resolving",
            'src="https://challenges.cloudflare.com/turnstile/v0/api.js"'
            in raw(crm, "/challenge.html")["body"],
        )

        reset(crm)
        set_switches(crm, field_in_iframe=True)
        framed = raw(crm, "/new-lead.html")["body"]
        check(
            "field_in_iframe renders the Email input inside a srcdoc iframe",
            "<iframe" in framed
            and "srcdoc=" in framed
            and "lead-email" in framed.split("srcdoc=", 1)[1]
            and 'data-testid="lead-email"' not in framed
            and 'id="lead-email-bridge"' in framed,
        )
        blind = save(crm, "", interest="Data course", company="Acme Analytics")
        check(
            "a frame-blind save (no email posted) is rejected and stores nothing",
            blind["status"] == 200
            and "email is required" in blind["body"]
            and state(crm)["leads"] == {},
        )

        reset(crm)
        set_switches(crm, disconnect_mid_save=True)
        pending = save(crm, SECOND_EMAIL)
        after_pending = state(crm)
        check(
            "disconnect_mid_save saves normally and flags disconnect_pending",
            "Lead created" in pending["body"]
            and after_pending["leads"] == {SECOND_EMAIL: 1}
            and after_pending["disconnect_pending"] is True,
        )

        reset(crm)
        set_switches(crm, required_company=True)
        missing_company = save(crm, SMTP_EMAIL, interest="Data course", company="")
        check(
            "required_company rejects an empty Company without storing",
            missing_company["status"] == 200
            and '<div role="alert" class="field-error">company is required</div>'
            in missing_company["body"]
            and "Lead created" not in missing_company["body"]
            and state(crm)["leads"] == {},
        )

        reset(crm)
        cleared = state(crm)
        check(
            "POST /__test__/reset empties the store and clears the switches",
            cleared["leads"] == {}
            and cleared["switches"] == server.DEFAULT_SWITCHES
            and cleared["saves"] == 0
            and cleared["disconnect_pending"] is False,
        )

        # --- 8. the partner origin -------------------------------------
        partner_root = raw(partner, "/")
        check(
            "the partner origin serves the partner page",
            partner_root["status"] == 200 and "<h1>Partner</h1>" in partner_root["body"],
        )
        check(
            "the partner origin is a different origin from the CRM",
            urlparse(crm).netloc != urlparse(partner).netloc,
        )
        check(
            "the CRM origin does not serve /partner.html and the partner origin has no test API",
            raw(crm, "/partner.html")["status"] == 404
            and raw(partner, "/__test__/state")["status"] == 404
            and raw(partner, "/__test__/reset", "POST")["status"] == 404,
        )

        # --- 9. static assets and 404s ---------------------------------
        check(
            "styles.css and app.js are served with the right types",
            raw(crm, "/styles.css")["headers"].get("content-type") == "text/css; charset=utf-8"
            and raw(crm, "/app.js")["headers"].get("content-type")
            == "text/javascript; charset=utf-8",
        )
        app_js = raw(crm, "/app.js")["body"]
        check(
            "app.js has the search fetch, the toast and the switch effects",
            "fetch(" in app_js
            and "/search.json" in app_js
            and "__SWITCHES__" in app_js
            and "stale_toast" in app_js
            and "field_in_iframe" in app_js
            and "slow_save_ms" in app_js
            and 'role", "cell' in app_js,
        )
        check(
            "interactive elements get real boxes in styles.css",
            "min-height: 36px" in styles["body"] and "display: block" in styles["body"],
        )
        check(
            "unknown routes answer 404",
            raw(crm, "/nope")["status"] == 404 and raw(crm, "/nope", "POST")["status"] == 404,
        )
    finally:
        for srv in (crm_server, partner_server):
            srv.shutdown()
            srv.server_close()

    print("")
    if FAILURES:
        print(f"SELFTEST FAILED ({len(FAILURES)} of {CHECKS} checks failed): {', '.join(FAILURES)}")
        return 1
    print(f"SELFTEST PASSED ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
