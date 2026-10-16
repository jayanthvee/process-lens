"""The real test apps (PL-027): Odoo 18 and Twenty CRM, running locally.

The two apps are stood up by `infra/test-apps/docker-compose.yml` and seeded by
`infra/test-apps/reset.py` (PL-022). This module is the thin client the golden
end-to-end tests use for three things the browser cannot answer:

* **Odoo, over XML-RPC** -- create the record a golden needs before it records
  (the tag the demonstration picks, the lead that is already there), and read
  back what a run left behind.
* **Twenty, over its own API** -- the same two duties for a workspace that has no
  XML-RPC.
* **Reset** -- run `reset.py odoo|twenty` so a golden can be re-run.

Everything here is test-side plumbing: no product code imports it, and the
credentials it uses are the committed test values in
`infra/test-apps/.env.example`.
"""

from __future__ import annotations

import http.client
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xmlrpc.client
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_APPS_DIR = REPO_ROOT / "infra" / "test-apps"

# `infra/test-apps` is not a package (the directory name has a dash), so the two
# modules the test apps already ship are imported by path, exactly as
# `reset.py` and `seed_odoo.py` themselves import them.
if str(TEST_APPS_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_APPS_DIR))

import seed_twenty  # noqa: E402  (needs the directory above on sys.path)
import testapps_env  # noqa: E402  (needs the directory above on sys.path)

# One XML-RPC call can legitimately be slow on a freshly started Odoo.
CALL_TIMEOUT_SECONDS = 120.0

# How long `reset.py` may take. Odoo installs five modules into a new database.
RESET_TIMEOUT_SECONDS = 900.0


def app_env() -> dict[str, str]:
    """The test apps' settings: `.env` when present, else the committed `.env.example`."""
    return testapps_env.load_env()


def setting(env: dict[str, str], key: str, default: str | None = None) -> str:
    return testapps_env.setting(env, key, default)


def odoo_base(env: dict[str, str]) -> str:
    """Odoo's origin, without a trailing slash."""
    port = setting(env, "ODOO_HTTP_PORT", "8069")
    return setting(env, "ODOO_URL", f"http://127.0.0.1:{port}").rstrip("/")


def twenty_base(env: dict[str, str]) -> str:
    """Twenty's origin, without a trailing slash."""
    configured = setting(env, "TWENTY_SERVER_URL", "")
    if configured:
        return configured.rstrip("/")
    port = setting(env, "TWENTY_HTTP_PORT", "3000")
    return f"http://127.0.0.1:{port}"


# ----------------------------------------------------------------- reset ---


def reset_app(target: str) -> dict[str, Any]:
    """Run `reset.py <target>` and return its summary.

    The ticket's requirement 4: a golden resets the app it drives before it runs,
    so the same test can be run again without hand-cleaning anything. A reset
    that fails is an assertion, never a silent pass: the test would otherwise
    measure whatever the last run left behind.
    """
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(TEST_APPS_DIR / "reset.py"), target],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=RESET_TIMEOUT_SECONDS,
    )
    elapsed = time.monotonic() - started
    if result.returncode != 0:
        raise AssertionError(
            f"reset.py {target} failed after {elapsed:.1f}s (rc={result.returncode})\n"
            f"stdout:\n{result.stdout[-4000:]}\nstderr:\n{result.stderr[-2000:]}"
        )
    tail = [line for line in result.stdout.strip().splitlines() if line.strip()]
    return {
        "target": target,
        "seconds": round(elapsed, 1),
        "summary": tail[-1] if tail else "",
    }


# ------------------------------------------------------------------ Odoo ---


class _TimeoutTransport(xmlrpc.client.Transport):
    """`xmlrpc.client.Transport` that actually applies a socket timeout.

    The stock transport builds its `HTTPConnection` without one, so a hung Odoo
    would block the test forever instead of failing it.
    """

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._sock_timeout = timeout

    def make_connection(self, host: str) -> http.client.HTTPConnection:
        if self._connection and host == self._connection[0]:
            return self._connection[1]
        chost, self._extra_headers, _x509 = self.get_host_info(host)
        self._connection = (host, http.client.HTTPConnection(chost, timeout=self._sock_timeout))
        return self._connection[1]


class Odoo:
    """A small XML-RPC client for the local Odoo: seed one record, read them back.

    It speaks only to the `common` and `object` endpoints; the database service
    (create/drop) belongs to `seed_odoo.py`, and a test never drops a database.
    """

    def __init__(self, env: dict[str, str]) -> None:
        self.env = env
        self.base = odoo_base(env)
        self.db = setting(env, "ODOO_DB_NAME", "processlens_test")
        self.login_name = setting(env, "ODOO_ADMIN_LOGIN", "admin")
        self.password = setting(env, "ODOO_ADMIN_PASSWORD", "ProcessLensTest18")
        transport = _TimeoutTransport(CALL_TIMEOUT_SECONDS)
        self._common = xmlrpc.client.ServerProxy(
            f"{self.base}/xmlrpc/2/common", transport=transport
        )
        self._object = xmlrpc.client.ServerProxy(
            f"{self.base}/xmlrpc/2/object", transport=transport
        )
        self._uid: int | None = None

    @property
    def uid(self) -> int:
        if self._uid is None:
            uid = self._common.authenticate(self.db, self.login_name, self.password, {})
            if not uid:
                raise AssertionError(
                    f"Odoo refused {self.login_name!r} on database {self.db!r} at {self.base}. "
                    "Run `python infra/test-apps/reset.py odoo` first."
                )
            self._uid = int(uid)
        return self._uid

    def call(
        self, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None
    ) -> Any:
        return self._object.execute_kw(
            self.db, self.uid, self.password, model, method, args, kwargs or {}
        )

    def count(self, model: str, domain: list[Any]) -> int:
        return int(self.call(model, "search_count", [domain]))

    def search(self, model: str, domain: list[Any], limit: int | None = None) -> list[int]:
        kwargs = {"limit": limit} if limit is not None else None
        return [int(one) for one in self.call(model, "search", [domain], kwargs)]

    def search_read(
        self, model: str, domain: list[Any], fields: list[str], limit: int | None = None
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"fields": fields}
        if limit is not None:
            kwargs["limit"] = limit
        return list(self.call(model, "search_read", [domain], kwargs))

    def create(self, model: str, values: dict[str, Any]) -> int:
        return int(self.call(model, "create", [values]))

    def fields(self, model: str, names: list[str]) -> dict[str, Any]:
        return dict(self.call(model, "fields_get", [names], {"attributes": ["type"]}))

    # -- the golden's own questions ---------------------------------------

    def leads_by_email(self, email: str) -> list[dict[str, Any]]:
        """Every `crm.lead` whose email is exactly `email`, with the fields the golden asserts."""
        return self.search_read(
            "crm.lead",
            [("email_from", "=", email)],
            ["name", "email_from", "phone", "partner_name", "user_id", "tag_ids", "type"],
        )

    def lead_count(self, email: str) -> int:
        return self.count("crm.lead", [("email_from", "=", email)])

    def tag_id(self, name: str, *, model: str = "crm.tag") -> int:
        """The id of a tag, creating it when it is missing.

        A demonstration that picks an *existing* tag records the option it saw.
        Letting the demonstration create the tag instead would record Odoo's
        "Create ..." suggestion, which is only offered while the tag is absent —
        so the replay would look for an option that is no longer there.
        """
        found = self.search(model, [("name", "=", name)], limit=1)
        if found:
            return found[0]
        return self.create(model, {"name": name})

    def salesperson_id(self, login: str) -> int:
        found = self.search("res.users", [("login", "=", login)], limit=1)
        if not found:
            raise AssertionError(
                f"Odoo has no res.users with login {login!r}; "
                "run `python infra/test-apps/reset.py odoo` to seed the salespeople"
            )
        return found[0]


def login_odoo(page: Any, env: dict[str, str], *, base: str | None = None) -> None:
    """Log the page into Odoo, as a person does, through the real login form."""
    origin = base or odoo_base(env)
    login = setting(env, "ODOO_ADMIN_LOGIN", "admin")
    password = setting(env, "ODOO_ADMIN_PASSWORD", "ProcessLensTest18")
    page.goto(f"{origin}/web/login", wait_until="domcontentloaded")
    if page.locator("input[name='login']").count():
        page.fill("input[name='login']", login)
        page.fill("input[name='password']", password)
        page.click("button[type='submit']")
        page.wait_for_load_state("load")
    # `/web/login` answers with the app switcher once the session cookie is set.
    for _ in range(40):
        if "/web/login" not in page.url:
            return
        page.wait_for_timeout(500)
    raise AssertionError(f"Odoo never left the login page (url={page.url})")


def login_twenty(page: Any, env: dict[str, str], *, base: str | None = None) -> None:
    """Log the page into Twenty, as a person does, through the real sign-in form.

    Twenty shows the sign-in form at `/auth/password` (the email step first on a fresh profile).
    The seeder created the login over Twenty's own API, so no sign-up happens here.
    """
    origin = base or twenty_base(env)
    email = setting(env, "TWENTY_LOGIN_EMAIL", "test@processlens.test")
    password = setting(env, "TWENTY_LOGIN_PASSWORD", "ProcessLensTest20")
    page.goto(f"{origin}/", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)

    def signed_in() -> bool:
        return "/auth/" not in page.url and page.locator("input[type='password']").count() == 0

    for _ in range(10):
        if signed_in():
            return
        email_box = page.locator("input[type='email'], input[name='email']").first
        if email_box.count() and email_box.is_visible() and not email_box.input_value():
            email_box.fill(email)
        for label in ("Continue", "Next"):
            button = page.get_by_role("button", name=label, exact=True)
            if button.count() and button.first.is_visible():
                button.first.click()
                page.wait_for_timeout(1500)
                break
        password_box = page.locator("input[type='password']").first
        if password_box.count() and password_box.is_visible():
            password_box.fill(password)
            submit = page.get_by_role("button", name=re.compile("sign in|log in|continue", re.I))
            if submit.count():
                submit.first.click()
            else:
                page.keyboard.press("Enter")
            page.wait_for_timeout(4000)
    if not signed_in():
        raise AssertionError(f"Twenty never left the sign-in page (url={page.url})")


# ---------------------------------------------------------------- Twenty ---


def twenty_client(env: dict[str, str]) -> tuple[Any, str]:
    """A Twenty client and a fresh workspace token (creating the workspace if needed)."""
    return seed_twenty.connect(env)


def twenty_list_people(env: dict[str, str]) -> list[dict[str, Any]]:
    """Every person in the workspace, so a test can assert on outcome, not on one read."""
    client, token = twenty_client(env)
    status, body = client.rest("GET", "/rest/people?limit=200", token=token)
    if status != 200:
        raise AssertionError(f"Twenty refused the people list: HTTP {status} {str(body)[:300]}")
    return list(body["data"]["people"])


def twenty_primary_email(person: dict[str, Any]) -> str:
    return str((person.get("emails") or {}).get("primaryEmail") or "")


def twenty_people_with_email(env: dict[str, str], email: str) -> list[dict[str, Any]]:
    wanted = email.strip().lower()
    return [
        person
        for person in twenty_list_people(env)
        if twenty_primary_email(person).strip().lower() == wanted
    ]


def twenty_company_names(env: dict[str, str]) -> list[str]:
    client, token = twenty_client(env)
    status, body = client.rest("GET", "/rest/companies?limit=200", token=token)
    if status != 200:
        raise AssertionError(f"Twenty refused the company list: HTTP {status} {str(body)[:300]}")
    return [str(company.get("name") or "") for company in body["data"]["companies"]]


def _urlopen(request: urllib.request.Request) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(request, timeout=CALL_TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except (TimeoutError, urllib.error.URLError) as error:
        raise AssertionError(f"cannot reach {request.full_url}: {error}") from error


def post_form(url: str, values: dict[str, str]) -> tuple[int, str]:
    """One form-encoded POST, for the odd endpoint that has no client here."""
    body = urllib.parse.urlencode(values).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("content-type", "application/x-www-form-urlencoded")
    return _urlopen(request)


def get_json(url: str) -> Any:
    request = urllib.request.Request(url, method="GET")
    status, text = _urlopen(request)
    if status != 200:
        raise AssertionError(f"GET {url} -> HTTP {status}: {text[:300]}")
    return json.loads(text)
