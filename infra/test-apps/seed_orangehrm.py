"""Seed OrangeHRM through its own REST API (v2), so the timesheet golden has what it needs.

OrangeHRM 5.7 runs at http://localhost:8070 (pinned in `docker-compose.yml`) and installs itself
unattended on the first `up`, so there is no browser step here either.

What a weekly timesheet needs is small but specific:

* **an employee who can sign in** -- OrangeHRM's API is session-based, and the person whose
  timesheet the golden fills must have their own ESS login;
* **two customers and four projects**, each with the two activities a timesheet row picks
  ("Development", "Meetings");
* **the employee as a project admin** of every project, so the app offers them all;
* **a weekly timesheet period**, so "the test week" is a week at all (OrangeHRM's install default
  is monthly); and
* **no timesheet yet for that week** -- a saved draft from an earlier run would make this run's
  first row an edit instead of a new row, so the seeder removes it.

Everything goes through `/web/index.php/api/v2/...` with the admin's own session cookie: the same
endpoints the app's Angular UI calls, so the data is exactly what a person would have made.
The names below are this module's own, so nothing here depends on another app's fixture.

Run it directly against a running stack:

    python infra/test-apps/seed_orangehrm.py
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from typing import Any

import testapps_env

Log = Callable[[str], None]

# The people, customers, projects and activities this app is seeded with. Synthetic, like the other
# seeders' data; `seed_data.py` is the source of truth for Odoo and Twenty only, and this module
# keeps its own so a change here cannot silently move another app's fixture.
EMPLOYEES: tuple[dict[str, str], ...] = (
    {
        "firstName": "Priya",
        "lastName": "Raman",
        "id": "9001",
        "username": "priya.raman",
        "timesheet": "yes",
    },
    {"firstName": "Jay", "lastName": "Vee", "id": "9002", "username": "jay.vee", "timesheet": "no"},
    {
        "firstName": "Ravi",
        "lastName": "Iyer",
        "id": "9003",
        "username": "ravi.iyer",
        "timesheet": "no",
    },
)

CUSTOMERS: tuple[dict[str, object], ...] = (
    {"name": "Northwind Traders", "projects": ("CRM Cleanup", "Quarterly Reporting")},
    {"name": "Contoso Ltd", "projects": ("Invoice Automation", "Onboarding Support")},
)

ACTIVITIES: tuple[str, ...] = ("Development", "Meetings")

# The employee whose timesheet the golden fills, and the week it fills: this week, Monday to
# Friday. Every run uses the week it runs in, so no date in the fixture goes stale.
TEST_EMPLOYEE = EMPLOYEES[0]


def test_week_start(today: date | None = None) -> date:
    """The Monday of the week `today` falls in."""
    day = today or date.today()
    return day - timedelta(days=day.weekday())


# ESS is user role 2 in OrangeHRM's own `user_role` table (1 is Admin).
ROLE_ESS = 2

# OrangeHRM's weekly timesheet period starts on this day (1 = Monday).
WEEKLY_START_DAY = "1"


class OrangeHRMError(RuntimeError):
    """The app refused a call, with its own answer attached."""


class OrangeHRM:
    """The admin's session against OrangeHRM, with the few calls the seeder makes."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self.settings = testapps_env.orangehrm_settings(env)
        self.origin = f"http://127.0.0.1:{self.settings['ORANGEHRM_HTTP_PORT']}"
        self.base = f"{self.origin}/web/index.php"
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    # -- the session ---------------------------------------------------------

    def sign_in(self, username: str, password: str) -> None:
        """Sign in the way the login page does: the CSRF token from the page, then the form post."""
        page = self._raw("GET", f"{self.base}/auth/login").decode("utf-8", "replace")
        match = re.search(r':token="&quot;([^&]+)&quot;"', page)
        if match is None:
            raise OrangeHRMError("the login page carried no CSRF token")
        body = urllib.parse.urlencode(
            {"_token": match.group(1), "username": username, "password": password}
        ).encode("utf-8")
        request = urllib.request.Request(f"{self.base}/auth/validate", data=body, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        with self.opener.open(request, timeout=60) as response:
            response.read()

    def sign_out(self) -> None:
        try:
            self._raw("GET", f"{self.base}/auth/logout")
        except (urllib.error.HTTPError, urllib.error.URLError):
            self.jar.clear()

    # -- the API -------------------------------------------------------------

    def _raw(self, method: str, url: str, payload: Any = None) -> bytes:
        data = None
        request = urllib.request.Request(url, method=method)
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request.add_header("Content-Type", "application/json")
            request.data = data
        request.add_header("Accept", "application/json")
        try:
            with self.opener.open(request, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")
            raise OrangeHRMError(f"{method} {url} -> HTTP {error.code}: {body[:600]}") from error
        except urllib.error.URLError as error:
            raise OrangeHRMError(f"cannot reach {url}: {error}") from error

    def call(self, method: str, path: str, payload: Any = None) -> Any:
        url = f"{self.base}/api/v2{path}"
        text = self._raw(method, url, payload).decode("utf-8", "replace")
        return json.loads(text) if text.strip() else None

    def get(self, path: str) -> Any:
        return self.call("GET", path)

    def post(self, path: str, payload: Any) -> Any:
        return self.call("POST", path, payload)

    def put(self, path: str, payload: Any) -> Any:
        return self.call("PUT", path, payload)

    def delete(self, path: str, payload: Any = None) -> Any:
        return self.call("DELETE", path, payload)

    # -- lookups the seeder needs -------------------------------------------

    def employee_by_name(self, first: str, last: str) -> int | None:
        found = self.get(
            "/pim/employees?limit=50&model=detailed&nameOrId="
            + urllib.parse.quote(f"{first} {last}")
        )
        for employee in (found or {}).get("data", []):
            name = employee.get("firstName", ""), employee.get("lastName", "")
            if name == (first, last):
                return int(employee["empNumber"])
        return None

    def customer_by_name(self, name: str) -> int | None:
        found = self.get("/time/customers?limit=50&name=" + urllib.parse.quote(name))
        for customer in (found or {}).get("data", []):
            if customer.get("name") == name:
                return int(customer["id"])
        return None

    def projects_of_customer(self, customer_id: int) -> dict[str, int]:
        found = self.get(f"/time/projects?limit=50&customerId={customer_id}")
        return {
            str(project.get("name")): int(project["id"])
            for project in (found or {}).get("data", [])
        }

    def activities_of_project(self, project_id: int) -> dict[str, int]:
        found = self.get(f"/time/project/{project_id}/activities?limit=50")
        return {
            str(activity.get("name")): int(activity["id"])
            for activity in (found or {}).get("data", [])
        }


# --------------------------------------------------------------------- the work


def ensure_weekly_period(app: OrangeHRM, log: Log) -> str:
    """Make the timesheet week start on Monday, and turn the Time module on.

    OrangeHRM 5.x timesheets are weekly, and the one thing that can be chosen is the first day of
    the week. This is the call Admin > Time > Configuration makes; it also enables the module's
    menu items, and until it has run the app refuses every Time call with 403 "Unauthorized".
    A stack that already has a period answers 400 "Already defined", which is a success here.
    """
    try:
        app.put("/time/time-sheet-period", {"startDay": WEEKLY_START_DAY})
    except OrangeHRMError as error:
        if "Already defined" in str(error):
            return "week starts on Monday (already set)"
        raise
    log("time period: week starts on Monday; Time module enabled")
    return "week starts on Monday"


def ensure_employee(app: OrangeHRM, log: Log) -> int:
    """The test employee, created through the PIM API when missing."""
    first, last = TEST_EMPLOYEE["firstName"], TEST_EMPLOYEE["lastName"]
    found = app.employee_by_name(first, last)
    if found is not None:
        return found
    created = app.post(
        "/pim/employees",
        {"firstName": first, "middleName": "", "lastName": last, "employeeId": TEST_EMPLOYEE["id"]},
    )
    emp_number = int(created["data"]["empNumber"])
    log(f"employee: {first} {last} (empNumber {emp_number})")
    return emp_number


def ensure_login(app: OrangeHRM, emp_number: int, log: Log) -> None:
    """The employee's own ESS login, so the timesheet is theirs and can be signed in to."""
    username = TEST_EMPLOYEE["username"]
    users = app.get(f"/admin/users?limit=50&empNumber={emp_number}")
    for user in (users or {}).get("data", []):
        if user.get("userName") == username:
            return
    app.post(
        "/admin/users",
        {
            "username": username,
            "password": app.settings["ORANGEHRM_EMPLOYEE_PASSWORD"],
            "status": True,
            "userRoleId": ROLE_ESS,
            "empNumber": emp_number,
        },
    )
    log(f"employee login: {username} (ESS)")


def ensure_customer(app: OrangeHRM, name: str, log: Log) -> int:
    found = app.customer_by_name(name)
    if found is not None:
        return found
    created = app.post("/time/customers", {"name": name, "description": "ProcessLens test data"})
    customer_id = int(created["data"]["id"])
    log(f"customer: {name} (#{customer_id})")
    return customer_id


def ensure_project(app: OrangeHRM, customer_id: int, name: str, employee: int, log: Log) -> int:
    existing = app.projects_of_customer(customer_id).get(name)
    if existing is not None:
        return existing
    created = app.post(
        "/time/projects",
        {
            "customerId": customer_id,
            "name": name,
            "description": "ProcessLens test data",
            "projectAdminsEmpNumbers": [employee],
        },
    )
    project_id = int(created["data"]["id"])
    log(f"project: {name} (#{project_id})")
    return project_id


def ensure_activities(app: OrangeHRM, project_id: int, log: Log) -> None:
    existing = app.activities_of_project(project_id)
    for activity in ACTIVITIES:
        if activity in existing:
            continue
        app.post(f"/time/project/{project_id}/activities", {"name": activity})
        log(f"activity: {activity} on project #{project_id}")


def clear_timesheets(app: OrangeHRM, employee: int, week_start: date, log: Log) -> int:
    """Remove everything the employee logged in the test week, so the run starts from nothing.

    A saved row left behind by an earlier run would turn this run's row into an edit of one the
    app already has: nothing new would be written, and the golden would be measuring the wrong
    thing. OrangeHRM has no call that deletes a whole timesheet -- only its rows -- so the week's
    rows go and the (now empty) timesheet stays, which is the state a person's Monday starts in.
    """
    week_end = week_start + timedelta(days=6)
    # The API filters by a date range (`fromDate`/`toDate`), not by one date.
    found = app.get(
        f"/time/employees/{employee}/timesheets"
        f"?fromDate={week_start.isoformat()}&toDate={week_end.isoformat()}&limit=50"
    )
    removed = 0
    for timesheet in (found or {}).get("data", []):
        start = date.fromisoformat(str(timesheet["startDate"]))
        if not (week_start <= start <= week_end):
            continue
        timesheet_id = int(timesheet["id"])
        entries = app.get(f"/time/employees/timesheets/{timesheet_id}/entries")
        ids = [int(entry["id"]) for entry in (entries or {}).get("data", [])]
        if ids:
            app.delete(f"/time/employees/timesheets/{timesheet_id}/entries", {"ids": ids})
            removed += len(ids)
    if removed:
        log(f"removed {removed} timesheet row(s) for the week of {week_start.isoformat()}")
    return removed


def ensure_week_timesheet(app: OrangeHRM, employee: int, week_start: date, log: Log) -> int:
    """Make sure the employee has a timesheet for the test week, ready to edit.

    A week with no timesheet is a week the page offers nothing to open: OrangeHRM creates the
    timesheet when a person asks for one, and asking is this call. Creating it here means the
    demonstration starts from "my timesheet for this week, with no rows".
    """
    week_end = week_start + timedelta(days=6)
    found = app.get(
        f"/time/employees/{employee}/timesheets"
        f"?fromDate={week_start.isoformat()}&toDate={week_end.isoformat()}&limit=50"
    )
    for timesheet in (found or {}).get("data", []):
        if str(timesheet.get("startDate")) == week_start.isoformat():
            return int(timesheet["id"])
    created = app.post(f"/time/employees/{employee}/timesheets", {"date": week_start.isoformat()})
    data = (created or {}).get("data", {})
    timesheet_id = int(data.get("id", 0))
    log(f"timesheet: week of {week_start.isoformat()} (#{timesheet_id}, empty)")
    return timesheet_id


def seed(env: Mapping[str, str], log: Log = print) -> dict[str, Any]:
    """Ensure everything the golden needs. Idempotent: a second run creates nothing new."""
    app = OrangeHRM(env)
    app.sign_in(app.settings["ORANGEHRM_ADMIN_USER"], app.settings["ORANGEHRM_ADMIN_PASSWORD"])
    try:
        period = ensure_weekly_period(app, log)
        employee = ensure_employee(app, log)
        ensure_login(app, employee, log)
        projects: list[dict[str, Any]] = []
        for customer in CUSTOMERS:
            customer_id = ensure_customer(app, customer["name"], log)
            for project in customer["projects"]:
                project_id = ensure_project(app, customer_id, project, employee, log)
                ensure_activities(app, project_id, log)
                projects.append(
                    {"customer": customer["name"], "project": project, "id": project_id}
                )
        removed = clear_timesheets(app, employee, test_week_start(), log)
        ensure_week_timesheet(app, employee, test_week_start(), log)
    finally:
        app.sign_out()
    return {
        "app": "orangehrm",
        "period": period,
        "employee": {
            "empNumber": employee,
            "username": TEST_EMPLOYEE["username"],
            "name": f"{TEST_EMPLOYEE['firstName']} {TEST_EMPLOYEE['lastName']}",
        },
        "projects": projects,
        "activities": list(ACTIVITIES),
        "week_start": test_week_start().isoformat(),
        "timesheets_removed": removed,
    }


def reset(env: Mapping[str, str], log: Log = print) -> dict[str, Any]:
    """OrangeHRM's reset is the seed: the data is ensured, and the week's timesheets are removed.

    Nothing else is wiped on purpose. Employees, projects and activities are shared by every run,
    and the only state a run leaves that a later run must not inherit is the timesheet itself.
    """
    summary = seed(env, log=log)
    summary["reset"] = True
    return summary


def main(argv: list[str] | None = None) -> int:
    del argv
    env = testapps_env.load_env()
    print(json.dumps(seed(env), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
