"""Seed the local Odoo 18 Community test app, purely over XML-RPC.

    python infra/test-apps/seed_odoo.py          # ensure the data, create nothing twice

`reset.py` imports this module and calls `seed(env, log=...)` or `reset(env, log=...)`;
both return the counts actually present in Odoo afterwards, so the caller can assert on
them. `seed` is idempotent: every record is looked up by its natural key first (lead,
customer and contact by email; product by `default_code`; user by login) and only what is
missing is created. Running it twice changes nothing and returns the same counts.

`reset` drops `ODOO_DB_NAME` through the XML-RPC database service, creates it again with
the configured admin login, installs the five modules the use cases need and then seeds.
Only the configured database is ever dropped.

The records come from the sibling `seed_data.py`; this module never keeps a copy of them.
Outgoing mail stays off: no `ir.mail_server` record is created, `seed` checks the model
and reports that count as `mail_servers`.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import sys
import time
import xmlrpc.client
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import seed_data  # noqa: E402  (needs this directory on sys.path)
import testapps_env  # noqa: E402  (needs this directory on sys.path)

# The modules the use cases drive. Installing `crm` pulls sales_team, calendar and utm.
MODULES: tuple[str, ...] = ("crm", "sale_management", "stock", "account", "calendar")

# A single XML-RPC call can legitimately take a while (database creation, module install).
CALL_TIMEOUT_SECONDS = 900.0
MODULE_INSTALL_TIMEOUT_SECONDS = 480.0
MODULE_POLL_SECONDS = 3.0


# --------------------------------------------------------------- transport ---


class _TimeoutTransport(xmlrpc.client.Transport):
    """`xmlrpc.client.Transport` that actually applies a socket timeout.

    The stock transport builds its `HTTPConnection` without a timeout, so a hung
    Odoo would block the caller forever. `_connection` is replaced wholesale, the
    way the standard library's own `make_connection` does it.
    """

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._sock_timeout = timeout

    def make_connection(self, host: str) -> http.client.HTTPConnection:
        if self._connection and host == self._connection[0]:
            return self._connection[1]
        chost, self._extra_headers, _x509 = self.get_host_info(host)
        self._connection = (
            host,
            http.client.HTTPConnection(chost, timeout=self._sock_timeout),
        )
        return self._connection[1]


# ---------------------------------------------------------------- settings ---


@dataclass(frozen=True)
class _Settings:
    url: str
    db: str
    admin_login: str
    admin_password: str
    sales_password: str
    master_password: str


def _settings(env: Mapping[str, str]) -> _Settings:
    """Read the Odoo settings through `testapps_env`, never from an env file here."""
    port = testapps_env.setting(env, "ODOO_HTTP_PORT", "8069")
    url = testapps_env.setting(env, "ODOO_URL", f"http://127.0.0.1:{port}").rstrip("/")
    return _Settings(
        url=url,
        db=testapps_env.setting(env, "ODOO_DB_NAME", "processlens_test"),
        admin_login=testapps_env.setting(env, "ODOO_ADMIN_LOGIN", "admin"),
        admin_password=testapps_env.setting(env, "ODOO_ADMIN_PASSWORD", "ProcessLensTest18"),
        sales_password=testapps_env.setting(env, "ODOO_SALES_PASSWORD", "SalesDemoPass18"),
        master_password=testapps_env.setting(env, "ODOO_MASTER_PASSWORD", "admin"),
    )


# ------------------------------------------------------------------ client ---


class _Odoo:
    """Thin XML-RPC client for the `db`, `common` and `object` endpoints."""

    def __init__(self, settings: _Settings) -> None:
        self.settings = settings
        self._uid: int | None = None
        self._db = xmlrpc.client.ServerProxy(
            f"{settings.url}/xmlrpc/2/db", transport=_TimeoutTransport(CALL_TIMEOUT_SECONDS)
        )
        self._common = xmlrpc.client.ServerProxy(
            f"{settings.url}/xmlrpc/2/common", transport=_TimeoutTransport(CALL_TIMEOUT_SECONDS)
        )
        self._object = xmlrpc.client.ServerProxy(
            f"{settings.url}/xmlrpc/2/object", transport=_TimeoutTransport(CALL_TIMEOUT_SECONDS)
        )

    # -- database service --------------------------------------------------

    def probe(self) -> dict[str, Any]:
        """Ask the server who it is; this is the reachability check."""
        try:
            return dict(self._common.version())
        except OSError as error:
            raise RuntimeError(
                f"Odoo is not answering at {self.settings.url} ({error}). Start it with "
                "docker compose -f infra/test-apps/docker-compose.yml up -d and wait for "
                "the odoo service to report healthy."
            ) from error

    def db_exists(self) -> bool:
        try:
            return bool(self._db.db_exist(self.settings.db))
        except xmlrpc.client.Fault:
            databases = self._db.list() or []
            return self.settings.db in databases

    def create_database(self) -> None:
        self._uid = None
        try:
            self._db.create_database(
                self.settings.master_password,
                self.settings.db,
                False,
                "en_US",
                self.settings.admin_password,
                self.settings.admin_login,
                "",
                "",
            )
        except xmlrpc.client.Fault as error:
            raise RuntimeError(
                f"could not create database {self.settings.db}: {error.faultString}. The "
                "master password must match the server's admin_passwd and database "
                "management must be enabled (list_db)."
            ) from error

    def drop_database(self) -> None:
        self._uid = None
        try:
            self._db.drop(self.settings.master_password, self.settings.db)
        except xmlrpc.client.Fault as error:
            raise RuntimeError(
                f"could not drop database {self.settings.db}: {error.faultString}. Check "
                "ODOO_MASTER_PASSWORD against the server's admin_passwd."
            ) from error

    # -- authenticated calls ----------------------------------------------

    def login(self) -> int:
        try:
            uid = self._common.authenticate(
                self.settings.db,
                self.settings.admin_login,
                self.settings.admin_password,
                {},
            )
        except (xmlrpc.client.Fault, OSError) as error:
            raise RuntimeError(
                f"authenticate as {self.settings.admin_login} in {self.settings.db} failed "
                f"({error}); the database may still be installing"
            ) from error
        if not uid:
            raise RuntimeError(
                f"{self.settings.admin_login} could not log into {self.settings.db}; check "
                "ODOO_ADMIN_LOGIN and ODOO_ADMIN_PASSWORD"
            )
        self._uid = int(uid)
        return self._uid

    def call(
        self,
        model: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: Mapping[str, Any] | None = None,
    ) -> Any:
        if self._uid is None:
            self.login()
        return self._object.execute_kw(
            self.settings.db,
            self._uid,
            self.settings.admin_password,
            model,
            method,
            list(args or []),
            dict(kwargs or {}),
        )

    def fields(self, model: str, names: list[str]) -> dict[str, Any]:
        result = self.call(model, "fields_get", [names], {"attributes": ["type"]})
        return dict(result or {})

    def search(self, model: str, domain: list[Any], limit: int | None = None) -> list[int]:
        kwargs: dict[str, Any] = {}
        if limit is not None:
            kwargs["limit"] = limit
        return [int(one) for one in self.call(model, "search", [domain], kwargs)]

    def search_read(
        self, model: str, domain: list[Any], fields: list[str], limit: int | None = None
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"fields": fields}
        if limit is not None:
            kwargs["limit"] = limit
        return list(self.call(model, "search_read", [domain], kwargs))

    def count(self, model: str, domain: list[Any]) -> int:
        return int(self.call(model, "search_count", [domain]))

    def create(
        self, model: str, values: dict[str, Any], context: Mapping[str, Any] | None = None
    ) -> int:
        kwargs = {"context": dict(context)} if context else None
        return int(self.call(model, "create", [values], kwargs))

    def write(
        self,
        model: str,
        ids: list[int],
        values: dict[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        kwargs = {"context": dict(context)} if context else None
        return bool(self.call(model, "write", [ids, values], kwargs))


def _first_id(odoo: _Odoo, model: str, domain: list[Any]) -> int | None:
    ids = odoo.search(model, domain, limit=1)
    return ids[0] if ids else None


def _external_id(odoo: _Odoo, module: str, name: str) -> int | None:
    rows = odoo.search_read(
        "ir.model.data",
        [("module", "=", module), ("name", "=", name)],
        ["res_id"],
        limit=1,
    )
    return int(rows[0]["res_id"]) if rows else None


# ----------------------------------------------------------------- modules ---


def _module_states(odoo: _Odoo) -> dict[str, str]:
    rows = odoo.search_read("ir.module.module", [("name", "in", list(MODULES))], ["name", "state"])
    return {str(row["name"]): str(row["state"]) for row in rows}


def _await_modules(odoo: _Odoo, log: Callable[[str], None]) -> dict[str, str]:
    """Poll until every module is `installed`, re-authenticating on each attempt.

    `button_immediate_install` reloads the registry, which can drop the in-flight
    call's session; that is expected and simply means "still installing".
    """
    deadline = time.monotonic() + MODULE_INSTALL_TIMEOUT_SECONDS
    states: dict[str, str] = {}
    attempt = 0
    while True:
        attempt += 1
        try:
            odoo.login()
            states = _module_states(odoo)
        except (RuntimeError, xmlrpc.client.Fault, OSError) as error:
            log(f"module poll {attempt}: not ready yet ({type(error).__name__}: {error})")
        if states and all(states.get(name) == "installed" for name in MODULES):
            return states
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"modules never reached 'installed' after {attempt} polls in "
                f"{MODULE_INSTALL_TIMEOUT_SECONDS:.0f}s; last state seen: {states}"
            )
        time.sleep(MODULE_POLL_SECONDS)


def ensure_modules(odoo: _Odoo, log: Callable[[str], None]) -> dict[str, str]:
    """Install the five use-case modules if they are not installed yet."""
    started = time.monotonic()
    states = _module_states(odoo)
    todo = [name for name in MODULES if states.get(name) != "installed"]
    if todo:
        log(f"installing modules: {', '.join(todo)}")
        ids = odoo.search("ir.module.module", [("name", "in", todo)])
        try:
            odoo.call("ir.module.module", "button_immediate_install", [ids])
        except (xmlrpc.client.Fault, OSError) as error:
            log(
                f"install call ended early ({type(error).__name__}: {error}); "
                "treating as still installing"
            )
        states = _await_modules(odoo, log)
    else:
        log("all five modules were already installed")
    log(f"modules ready in {time.monotonic() - started:.1f}s: {states}")
    return states


# ------------------------------------------------------------------ records ---


def _seed_salespeople(odoo: _Odoo, settings: _Settings, log: Callable[[str], None]) -> int:
    fields = odoo.fields("res.users", ["groups_id", "group_ids"])
    group_field = "group_ids" if "group_ids" in fields else "groups_id"
    group_ids = [
        group_id
        for group_id in (
            _external_id(odoo, "base", "group_user"),
            _external_id(odoo, "sales_team", "group_sale_salesman"),
        )
        if group_id
    ]
    created = 0
    for person in seed_data.ODOO_SALESPEOPLE:
        if _first_id(odoo, "res.users", [("login", "=", person["login"])]):
            continue
        odoo.create(
            "res.users",
            {
                "name": person["name"],
                "login": person["login"],
                "email": person["email"],
                "password": settings.sales_password,
                group_field: [(6, 0, group_ids)],
            },
        )
        created += 1
    log(
        f"salespeople: {created} created, {len(seed_data.ODOO_SALESPEOPLE) - created} already there"
    )
    return created


def _seed_leads(odoo: _Odoo, log: Callable[[str], None]) -> int:
    sources: dict[str, int] = {}
    created = 0
    for lead in seed_data.ODOO_LEADS:
        if _first_id(odoo, "crm.lead", [("email_from", "=", lead["email"])]):
            continue
        source_id = sources.get(lead["source"])
        if source_id is None:
            source_id = _first_id(odoo, "utm.source", [("name", "=", lead["source"])])
            if source_id is None:
                source_id = odoo.create("utm.source", {"name": lead["source"]})
            sources[lead["source"]] = source_id
        odoo.create(
            "crm.lead",
            {
                "name": lead["name"],
                "partner_name": lead["company"],
                "email_from": lead["email"],
                "phone": lead["phone"],
                "source_id": source_id,
                "type": "lead",
            },
        )
        created += 1
    log(f"leads: {created} created, {len(seed_data.ODOO_LEADS) - created} already there")
    return created


def _internal_location(odoo: _Odoo) -> int | None:
    return _first_id(odoo, "stock.location", [("usage", "=", "internal")])


def _set_on_hand(odoo: _Odoo, product_id: int, location_id: int, quantity: float) -> None:
    """Put `quantity` on hand by counting it in the inventory location.

    `qty_available` is computed from `stock.quant`, so it is never written directly.
    The count goes in through `inventory_quantity_auto_apply`, whose inverse applies
    it immediately (`_set_inventory_quantity` -> `_apply_inventory`). That indirection
    is not a preference: `action_apply_inventory` returns `None`, and Odoo marshals its
    XML-RPC replies with `OdooMarshaller(allow_none=False)`, so calling it at all fails
    with "cannot marshal None" — the count is applied server-side and then the reply
    cannot be serialized. Writing the field also returns a plain `True`.

    The write is a no-op when the count already matches, so this stays idempotent.
    """
    context = {"inventory_mode": True}
    domain = [("product_id", "=", product_id), ("location_id", "=", location_id)]
    quant_ids = odoo.search("stock.quant", domain)
    if not quant_ids:
        quant_ids = [
            odoo.create(
                "stock.quant",
                {"product_id": product_id, "location_id": location_id},
                context,
            )
        ]
    odoo.write("stock.quant", quant_ids, {"inventory_quantity_auto_apply": quantity}, context)


def _seed_products(odoo: _Odoo, log: Callable[[str], None]) -> int:
    template_fields = odoo.fields("product.template", ["type", "is_storable"])
    # Odoo 17+ spells a storable product "consu with is_storable"; older ones use type=product.
    storable = {"is_storable": True} if "is_storable" in template_fields else {}
    product_type = "consu" if storable else "product"
    location_id = _internal_location(odoo)
    if location_id is None:
        raise RuntimeError("no internal stock.location found; is the stock module installed?")
    created = 0
    for product in seed_data.ODOO_PRODUCTS:
        code = product["default_code"]
        template_id = _first_id(odoo, "product.template", [("default_code", "=", code)])
        if template_id is None:
            template_id = odoo.create(
                "product.template",
                {
                    "name": product["name"],
                    "default_code": code,
                    "list_price": float(product["list_price"]),
                    "standard_price": float(product["standard_price"]),
                    "type": product_type,
                    **storable,
                },
            )
            created += 1
        variant_id = _first_id(odoo, "product.product", [("default_code", "=", code)])
        if variant_id is None:
            variant_id = _first_id(odoo, "product.product", [("product_tmpl_id", "=", template_id)])
        if variant_id is None:
            raise RuntimeError(f"{code}: template {template_id} has no product variant")
        _set_on_hand(odoo, variant_id, location_id, float(product["qty_available"]))
    log(f"products: {created} created, {len(seed_data.ODOO_PRODUCTS) - created} already there")
    return created


def _on_hand(odoo: _Odoo, code: str) -> float | None:
    rows = odoo.search_read(
        "product.product", [("default_code", "=", code)], ["qty_available"], limit=1
    )
    return float(rows[0]["qty_available"]) if rows else None


def _verify_on_hand(odoo: _Odoo) -> list[str]:
    """Read `qty_available` back for every seeded product and report differences."""
    mismatches = []
    for product in seed_data.ODOO_PRODUCTS:
        expected = float(product["qty_available"])
        seen = _on_hand(odoo, product["default_code"])
        if seen != expected:
            mismatches.append(f"{product['default_code']}: expected {expected}, read {seen}")
    return mismatches


def _income_account(odoo: _Odoo) -> int | None:
    fields = odoo.fields("account.account", ["account_type"])
    if "account_type" in fields:
        return _first_id(odoo, "account.account", [("account_type", "=", "income")])
    return _first_id(odoo, "account.account", [("user_type_id.name", "=", "Income")])


def _seed_customers(odoo: _Odoo, log: Callable[[str], None]) -> tuple[int, int]:
    account_id = _income_account(odoo)
    today = dt.date.today().isoformat()
    customers = 0
    invoices = 0
    for customer in seed_data.ODOO_CUSTOMERS:
        partner_id = _first_id(odoo, "res.partner", [("email", "=", customer["email"])])
        if partner_id is None:
            partner_id = odoo.create(
                "res.partner", {"name": customer["name"], "email": customer["email"]}
            )
            customers += 1
        domain = [("ref", "=", customer["invoice_ref"]), ("move_type", "=", "out_invoice")]
        if _first_id(odoo, "account.move", domain):
            continue
        line: dict[str, Any] = {
            "name": f"{customer['name']} - {customer['invoice_ref']}",
            "quantity": 1.0,
            "price_unit": float(customer["amount"]),
            "tax_ids": [(6, 0, [])],
        }
        if account_id:
            line["account_id"] = account_id
        move_id = odoo.create(
            "account.move",
            {
                "move_type": "out_invoice",
                "partner_id": partner_id,
                "ref": customer["invoice_ref"],
                "invoice_date": today,
                "invoice_line_ids": [(0, 0, line)],
            },
        )
        odoo.call("account.move", "action_post", [[move_id]])
        invoices += 1
    log(f"customers: {customers} created; invoices: {invoices} posted")
    return customers, invoices


def _seed_enrollment(odoo: _Odoo, log: Callable[[str], None]) -> int:
    tag_ids: dict[str, int] = {}
    for name in seed_data.ODOO_ENROLLMENT_TAGS:
        tag_id = _first_id(odoo, "res.partner.category", [("name", "=", name)])
        if tag_id is None:
            tag_id = odoo.create("res.partner.category", {"name": name})
        tag_ids[name] = tag_id
    created = 0
    for contact in seed_data.ODOO_ENROLLMENT_CONTACTS:
        wanted = sorted({tag_ids[name] for name in contact["tags"] if name in tag_ids})
        partner_id = _first_id(odoo, "res.partner", [("email", "=", contact["email"])])
        if partner_id is None:
            odoo.create(
                "res.partner",
                {
                    "name": contact["name"],
                    "email": contact["email"],
                    "phone": contact["phone"],
                    "category_id": [(6, 0, wanted)],
                },
            )
            created += 1
            continue
        rows = odoo.call("res.partner", "read", [[partner_id], ["category_id"]])
        current = {int(tag) for tag in (rows[0]["category_id"] if rows else [])}
        missing = [tag for tag in wanted if tag not in current]
        if missing:
            odoo.write("res.partner", [partner_id], {"category_id": [(4, tag) for tag in missing]})
    log(
        f"enrollment: {created} contacts created, "
        f"{len(seed_data.ODOO_ENROLLMENT_CONTACTS) - created} already there; {len(tag_ids)} tags"
    )
    return created


def _seed_all(odoo: _Odoo, settings: _Settings, log: Callable[[str], None]) -> None:
    _seed_salespeople(odoo, settings, log)
    _seed_leads(odoo, log)
    _seed_products(odoo, log)
    _seed_customers(odoo, log)
    _seed_enrollment(odoo, log)


# ----------------------------------------------------------------- summary ---


def _count_present(odoo: _Odoo, model: str, field: str, values: list[str]) -> int:
    return odoo.count(model, [(field, "in", list(values))])


def _summary(odoo: _Odoo, settings: _Settings) -> dict[str, Any]:
    """The counts that are really there now, keyed by the natural key of each group."""
    return {
        "database": settings.db,
        "url": settings.url,
        "salespeople": _count_present(
            odoo, "res.users", "login", [p["login"] for p in seed_data.ODOO_SALESPEOPLE]
        ),
        "leads": _count_present(
            odoo, "crm.lead", "email_from", [lead["email"] for lead in seed_data.ODOO_LEADS]
        ),
        "products": _count_present(
            odoo,
            "product.template",
            "default_code",
            [p["default_code"] for p in seed_data.ODOO_PRODUCTS],
        ),
        "customers": _count_present(
            odoo, "res.partner", "email", [c["email"] for c in seed_data.ODOO_CUSTOMERS]
        ),
        "invoices": odoo.count(
            "account.move",
            [
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("ref", "in", [c["invoice_ref"] for c in seed_data.ODOO_CUSTOMERS]),
            ],
        ),
        "contacts": _count_present(
            odoo,
            "res.partner",
            "email",
            [c["email"] for c in seed_data.ODOO_ENROLLMENT_CONTACTS],
        ),
        "tags": _count_present(
            odoo, "res.partner.category", "name", list(seed_data.ODOO_ENROLLMENT_TAGS)
        ),
        "mail_servers": odoo.count("ir.mail_server", []),
        "stock_mismatches": _verify_on_hand(odoo),
        "modules": _module_states(odoo),
    }


def _report_mail(odoo: _Odoo, log: Callable[[str], None]) -> None:
    servers = odoo.count("ir.mail_server", [])
    if servers:
        log(f"WARNING: {servers} ir.mail_server record(s) exist; outgoing mail may be configured")
    else:
        log("ir.mail_server is empty: outgoing mail stays off")


# -------------------------------------------------------------------- api ---


def seed(env: Mapping[str, str], log: Callable[[str], None] = print) -> dict[str, Any]:
    """Create the database if needed, install the modules, ensure the records exist."""
    settings = _settings(env)
    started = time.monotonic()
    odoo = _Odoo(settings)
    log(f"odoo: {settings.url} db={settings.db}")
    version = odoo.probe()
    log(f"server {version.get('server_version')} at {version.get('protocol_version')}")

    phase = time.monotonic()
    if odoo.db_exists():
        log(f"database {settings.db} already exists")
    else:
        log(f"database {settings.db} is missing; creating it")
        odoo.create_database()
        log(f"database created in {time.monotonic() - phase:.1f}s")

    odoo.login()
    ensure_modules(odoo, log)
    _seed_all(odoo, settings, log)
    _report_mail(odoo, log)
    summary = _summary(odoo, settings)
    summary["elapsed_seconds"] = round(time.monotonic() - started, 1)
    log(f"seed done in {summary['elapsed_seconds']:.1f}s: {json.dumps(summary, sort_keys=True)}")
    return summary


def reset(env: Mapping[str, str], log: Callable[[str], None] = print) -> dict[str, Any]:
    """Drop `ODOO_DB_NAME`, create it again, install the modules and seed."""
    settings = _settings(env)
    started = time.monotonic()
    odoo = _Odoo(settings)
    log(f"odoo: {settings.url} db={settings.db}")
    version = odoo.probe()
    log(f"server {version.get('server_version')} at {version.get('protocol_version')}")

    phase = time.monotonic()
    if odoo.db_exists():
        odoo.drop_database()
        log(f"dropped {settings.db} in {time.monotonic() - phase:.1f}s")
    else:
        log(f"{settings.db} did not exist; nothing to drop")
    phase = time.monotonic()
    odoo.create_database()
    log(f"created {settings.db} in {time.monotonic() - phase:.1f}s")

    odoo.login()
    phase = time.monotonic()
    ensure_modules(odoo, log)
    log(f"module phase took {time.monotonic() - phase:.1f}s")

    phase = time.monotonic()
    _seed_all(odoo, settings, log)
    log(f"record phase took {time.monotonic() - phase:.1f}s")
    _report_mail(odoo, log)

    summary = _summary(odoo, settings)
    summary["elapsed_seconds"] = round(time.monotonic() - started, 1)
    log(f"reset done in {summary['elapsed_seconds']:.1f}s: {json.dumps(summary, sort_keys=True)}")
    return summary


def main() -> int:
    summary = seed(testapps_env.load_env())
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
