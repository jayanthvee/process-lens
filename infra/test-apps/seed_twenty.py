"""Seed Twenty CRM through its own API.

Twenty runs at http://localhost:3000 (twentcrm/twenty, pinned in
`docker-compose.yml`). This module owns the whole life cycle of the test
workspace, and it needs no manual step: the first workspace is created over the
API exactly the way the browser does it.

The API surface, as the pinned build actually behaves:

* `POST /metadata` is the core GraphQL API (auth, workspace, API keys). The data
  API is `POST /graphql` and the REST API is `/rest/...`; `signUp` only exists on
  `/metadata`.
* A workspace is reachable in four steps: `signUp` (or `signIn`) -> workspace
  agnostic token -> `signUpInNewWorkspace` -> `getAuthTokensFromLoginToken`, and
  then `activateWorkspace`, which provisions the workspace schema and its
  standard objects. Until that call the workspace sits in `PENDING_CREATION` and
  every data request answers "No data sources found".
* A token minted *before* activation cannot be used afterwards, so the flow below
  deliberately signs in again and mints a fresh one.
* `activateWorkspace` is idempotent: on an active workspace it returns it.
* `DELETE /rest/{object}` refuses to run without a filter ("Filters are mandatory
  for bulk destroy operations"), so clearing uses a match-all filter. Without
  `soft_delete=true` that is a hard delete.

A new workspace also arrives with Twenty's own few sample people and companies
(the standard prefill data). `reset` clears those along with everything else, so
the workspace ends up holding exactly the records in `seed_data.py`.

Run it directly to seed an already-running stack:

    python infra/test-apps/seed_twenty.py
"""

from __future__ import annotations

import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

import seed_data
import testapps_env

# Imported as siblings: this directory is on sys.path when the file is run as a
# script or through `reset.py`, and the directory name (`test-apps`) is not a
# legal Python package name, so a normal relative import is not available.

Log = Callable[[str], None]

# A match-all filter: bulk destroy refuses an unfiltered call, and every record
# was created before the year 2100.
MATCH_ALL_FILTER = "createdAt[lt]:2100-01-01T00:00:00.000Z"

# Cleared before re-seeding, most-referencing object first.
CLEARED_OBJECTS = ("notes", "tasks", "opportunities", "people", "companies")

SIGN_UP = """
mutation ($email: String!, $password: String!) {
  signUp(email: $email, password: $password) {
    tokens { accessOrWorkspaceAgnosticToken { token } }
    availableWorkspaces { availableWorkspacesForSignIn { id loginToken } }
  }
}
"""

SIGN_IN = """
mutation ($email: String!, $password: String!) {
  signIn(email: $email, password: $password) {
    tokens { accessOrWorkspaceAgnosticToken { token } }
    availableWorkspaces { availableWorkspacesForSignIn { id loginToken } }
  }
}
"""

SIGN_UP_IN_NEW_WORKSPACE = """
mutation ($input: SignUpInNewWorkspaceInput) {
  signUpInNewWorkspace(input: $input) {
    loginToken { token }
    workspace { id }
  }
}
"""

EXCHANGE_LOGIN_TOKEN = """
mutation ($loginToken: String!, $origin: String!) {
  getAuthTokensFromLoginToken(loginToken: $loginToken, origin: $origin) {
    tokens { accessOrWorkspaceAgnosticToken { token } }
  }
}
"""

ACTIVATE_WORKSPACE = """
mutation { activateWorkspace(data: {}) { id activationStatus } }
"""


class TwentyError(RuntimeError):
    """A Twenty API call did not do what the caller needed."""


class TwentyClient:
    """A tiny Twenty client: GraphQL on the core API, REST for records."""

    def __init__(self, base_url: str, log: Log = print) -> None:
        self.base_url = base_url.rstrip("/")
        self.log = log
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    # ---------------------------------------------------------------- http ---
    def _send(
        self,
        method: str,
        path: str,
        payload: Any = None,
        token: str | None = None,
        timeout: float = 600.0,
    ) -> tuple[int, str]:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Origin": self.base_url, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(
            self.base_url + path, data=body, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()
        except urllib.error.URLError as error:
            raise TwentyError(
                f"cannot reach Twenty at {self.base_url}{path}: {error.reason}. "
                "Is the stack up (docker compose -f infra/test-apps/docker-compose.yml up -d)?"
            ) from error

    def graphql(self, query: str, variables: dict | None = None, token: str | None = None) -> dict:
        """Run a core-API GraphQL document, raising on GraphQL errors."""
        status, text = self._send(
            "POST", "/metadata", {"query": query, "variables": variables or {}}, token
        )
        return self._unwrap_graphql(status, text, query)

    def graphql_errors(self, query: str, variables: dict | None = None) -> list[dict]:
        """Run a GraphQL document and return its errors instead of raising."""
        status, text = self._send(
            "POST", "/metadata", {"query": query, "variables": variables or {}}
        )
        if status != 200:
            return [{"message": f"HTTP {status}: {text[:300]}"}]
        try:
            return json.loads(text).get("errors", [])
        except json.JSONDecodeError:
            return [{"message": text[:300]}]

    @staticmethod
    def _unwrap_graphql(status: int, text: str, query: str) -> dict:
        if status != 200:
            raise TwentyError(f"Twenty answered HTTP {status}: {text[:400]}")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise TwentyError(
                f"Twenty answered with something that is not JSON: {text[:400]}"
            ) from error
        if payload.get("errors"):
            messages = "; ".join(str(error.get("message")) for error in payload["errors"])
            raise TwentyError(f"GraphQL error for {query.splitlines()[1].strip()}: {messages}")
        return payload["data"]

    def rest(
        self, method: str, path: str, payload: Any = None, token: str | None = None
    ) -> tuple[int, Any]:
        """Call the REST API, returning (status, parsed body or raw text)."""
        status, text = self._send(method, path, payload, token)
        try:
            return status, json.loads(text)
        except json.JSONDecodeError:
            return status, text

    # ---------------------------------------------------------------- auth ---
    def sign_up(self, email: str, password: str) -> dict:
        return self.graphql(SIGN_UP, {"email": email, "password": password})

    def sign_in(self, email: str, password: str) -> dict:
        return self.graphql(SIGN_IN, {"email": email, "password": password})

    def sign_in_or_up(self, email: str, password: str, log: Log) -> dict:
        """Sign in; create the login on a fresh instance."""
        errors = self.graphql_errors(SIGN_IN, {"email": email, "password": password})
        if not errors:
            return self.sign_in(email, password)
        joined = json.dumps(errors).lower()
        if "user_not_found" not in joined and "not found" not in joined:
            raise TwentyError(f"sign-in failed: {json.dumps(errors)[:300]}")
        log(f"no login for {email} yet, creating it")
        return self.sign_up(email, password)

    def exchange_login_token(self, login_token: str) -> str:
        data = self.graphql(
            EXCHANGE_LOGIN_TOKEN,
            {"loginToken": login_token, "origin": self.base_url},
        )
        return data["getAuthTokensFromLoginToken"]["tokens"]["accessOrWorkspaceAgnosticToken"][
            "token"
        ]


def _login_token_and_agnostic(auth_payload: dict, mutation: str) -> tuple[str | None, str]:
    """Pull the workspace login token and the workspace-agnostic token out of a payload."""
    result = auth_payload[mutation]
    workspaces = result["availableWorkspaces"]["availableWorkspacesForSignIn"]
    login_token = workspaces[0]["loginToken"] if workspaces else None
    agnostic = result["tokens"]["accessOrWorkspaceAgnosticToken"]["token"]
    return login_token, agnostic


def workspace_ready(client: TwentyClient, env: Mapping[str, str], log: Log) -> None:
    """Make sure the workspace exists and is provisioned.

    Idempotent: on a provisioned workspace this only signs in and activates what
    is already active.
    """
    email = testapps_env.setting(env, "TWENTY_LOGIN_EMAIL", "test@processlens.test")
    password = testapps_env.setting(env, "TWENTY_LOGIN_PASSWORD", "ProcessLensTest20")
    display_name = testapps_env.setting(env, "TWENTY_WORKSPACE_NAME", "ProcessLens Test")

    auth = client.sign_in_or_up(email, password, log)
    mutation = "signUp" if "signUp" in auth else "signIn"
    login_token, agnostic = _login_token_and_agnostic(auth, mutation)

    if login_token is None:
        log(f"creating the workspace {display_name!r}")
        created = client.graphql(
            SIGN_UP_IN_NEW_WORKSPACE, {"input": {"displayName": display_name}}, agnostic
        )
        login_token = created["signUpInNewWorkspace"]["loginToken"]["token"]

    log("activating the workspace (idempotent; provisions it on first run)")
    started = time.monotonic()
    activated = client.graphql(ACTIVATE_WORKSPACE, token=client.exchange_login_token(login_token))
    log(
        "workspace {id} is {status} after {seconds:.1f}s".format(
            id=activated["activateWorkspace"]["id"][:8],
            status=activated["activateWorkspace"]["activationStatus"],
            seconds=time.monotonic() - started,
        )
    )


def _workspace_token(client: TwentyClient, env: Mapping[str, str], log: Log) -> str:
    """Sign in again after activation: a pre-activation token is refused."""
    email = testapps_env.setting(env, "TWENTY_LOGIN_EMAIL", "test@processlens.test")
    password = testapps_env.setting(env, "TWENTY_LOGIN_PASSWORD", "ProcessLensTest20")
    auth = client.sign_in(email, password)
    login_token, _ = _login_token_and_agnostic(auth, "signIn")
    if login_token is None:
        raise TwentyError("signed in, but Twenty reported no workspace to sign in to")
    return client.exchange_login_token(login_token)


def connect(env: Mapping[str, str], log: Log = print) -> tuple[TwentyClient, str]:
    """Return a client and a fresh workspace token, creating the workspace if needed."""
    base_url = testapps_env.setting(env, "TWENTY_SERVER_URL", "")
    if not base_url:
        port = testapps_env.setting(env, "TWENTY_HTTP_PORT", "3000")
        base_url = f"http://localhost:{port}"
    client = TwentyClient(base_url, log)
    workspace_ready(client, env, log)
    return client, _workspace_token(client, env, log)


def _count(client: TwentyClient, token: str, object_name: str) -> int:
    status, body = client.rest("GET", f"/rest/{object_name}?limit=1", token=token)
    if status != 200:
        raise TwentyError(f"cannot read {object_name}: HTTP {status} {str(body)[:200]}")
    if isinstance(body, dict) and "totalCount" in body:
        return int(body["totalCount"])
    return len(body["data"][object_name])


def _list_all(client: TwentyClient, token: str, object_name: str) -> list[dict]:
    status, body = client.rest("GET", f"/rest/{object_name}?limit=200", token=token)
    if status != 200:
        raise TwentyError(f"cannot list {object_name}: HTTP {status} {str(body)[:200]}")
    return body["data"][object_name]


def clear(client: TwentyClient, token: str, log: Log) -> dict[str, int]:
    """Hard-delete every record of the objects this seeder owns."""
    removed: dict[str, int] = {}
    for object_name in CLEARED_OBJECTS:
        before = _count(client, token, object_name)
        if before == 0:
            removed[object_name] = 0
            continue
        status, body = client.rest(
            "DELETE", f"/rest/{object_name}?filter={MATCH_ALL_FILTER}", token=token
        )
        if status != 200:
            raise TwentyError(f"clearing {object_name} failed: HTTP {status} {str(body)[:200]}")
        after = _count(client, token, object_name)
        removed[object_name] = before - after
        log(f"cleared {removed[object_name]} {object_name}")
    return removed


def _people_payload() -> list[dict]:
    return [
        {
            "name": {"firstName": person["first_name"], "lastName": person["last_name"]},
            "emails": {"primaryEmail": person["email"]},
            "jobTitle": person["job_title"],
            "phones": {"primaryPhoneNumber": person["phone"]},
        }
        for person in seed_data.TWENTY_PEOPLE
    ]


def _companies_payload() -> list[dict]:
    return [
        {"name": company["name"], "domainName": {"primaryLinkUrl": company["domain"]}}
        for company in seed_data.TWENTY_COMPANIES
    ]


def _primary_email(person: dict) -> str:
    return (person.get("emails") or {}).get("primaryEmail") or ""


def _ensure_records(client: TwentyClient, token: str, log: Log) -> dict[str, int]:
    """Create only what is missing, so a second run changes nothing."""
    existing_emails = {_primary_email(person) for person in _list_all(client, token, "people")}
    wanted_people = _people_payload()
    missing_people = [
        person
        for person in wanted_people
        if person["emails"]["primaryEmail"] not in existing_emails
    ]
    if missing_people:
        status, body = client.rest("POST", "/rest/batch/people", missing_people, token)
        if status not in (200, 201):
            raise TwentyError(f"creating people failed: HTTP {status} {str(body)[:200]}")
    log(
        f"people: {len(wanted_people) - len(missing_people)} already there, "
        f"{len(missing_people)} created"
    )

    existing_names = {company.get("name") for company in _list_all(client, token, "companies")}
    wanted_companies = _companies_payload()
    missing_companies = [
        company for company in wanted_companies if company["name"] not in existing_names
    ]
    if missing_companies:
        status, body = client.rest("POST", "/rest/batch/companies", missing_companies, token)
        if status not in (200, 201):
            raise TwentyError(f"creating companies failed: HTTP {status} {str(body)[:200]}")
    log(
        f"companies: {len(wanted_companies) - len(missing_companies)} already there, "
        f"{len(missing_companies)} created"
    )
    return {"people_created": len(missing_people), "companies_created": len(missing_companies)}


def seed(env: Mapping[str, str], log: Log = print) -> dict[str, Any]:
    """Ensure the workspace is active and every seeded record exists."""
    client, token = connect(env, log)
    created = _ensure_records(client, token, log)
    summary = {
        "app": "twenty",
        "url": client.base_url,
        "people": _count(client, token, "people"),
        "companies": _count(client, token, "companies"),
        **created,
    }
    log(f"twenty seeded: {summary['people']} people, {summary['companies']} companies")
    return summary


def reset(env: Mapping[str, str], log: Log = print) -> dict[str, Any]:
    """Drop Twenty's records and seed them again."""
    started = time.monotonic()
    client, token = connect(env, log)
    clear(client, token, log)
    created = _ensure_records(client, token, log)
    summary = {
        "app": "twenty",
        "url": client.base_url,
        "people": _count(client, token, "people"),
        "companies": _count(client, token, "companies"),
        "seconds": round(time.monotonic() - started, 1),
        **created,
    }
    log(
        f"twenty reset in {summary['seconds']}s: "
        f"{summary['people']} people, {summary['companies']} companies"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = testapps_env.load_env()
    result = reset(env) if "--reset" in argv else seed(env)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
