"""The 500-row run: one recording on real Odoo, replayed for 500 customers.

Opt-in only (``PROCESSLENS_E2E_SCALE=1``): it takes hours and drives Odoo the whole time, so it
never runs with the ordinary e2e suite. It answers what the three-row goldens cannot: how long a
real batch takes per row, whether rows get stuck at scale, and whether the ledger, the run page's
API and Odoo agree at the end. Its numbers land in ``.cache/scale500/summary.json``.

The demonstration and the compile are the quotation golden's own (``record_new_quote``,
``compile_via_popup``), so a difference here is a difference of scale, not of setup.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from .test_odoo_quote_golden import (
    DEMO_CUSTOMER,
    DEMO_PRODUCT,
    ROWS,
    column_for,
    record_new_quote,
    recorded_columns,
    rows_for,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.real_apps,
    pytest.mark.skipif(
        os.environ.get("PROCESSLENS_E2E_SCALE") != "1",
        reason="the 500-row run is opt-in: set PROCESSLENS_E2E_SCALE=1",
    ),
]

ROW_COUNT = int(os.environ.get("PROCESSLENS_E2E_SCALE_ROWS", "500"))
OUT = Path(__file__).resolve().parents[2] / ".cache" / "scale500"
# Generous on purpose: the point is to measure, not to fail early. Six hours covers 40 s a row.
RUN_SECONDS = 6 * 3600


def _note(message: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "trace.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%H:%M:%S')} {message.rstrip()}\n")


def test_odoo_500_quotations(odoo_harness, odoo_app) -> None:
    harness = odoo_harness
    _note(f"=== scale run: {ROW_COUNT} rows ===")

    # 1. The customers: one per row, so every row has its own record key. Created through the
    #    API, never through the browser, and named so a reset's seed never collides with them.
    odoo_app.partner_id(DEMO_CUSTOMER)
    odoo_app.product_id(DEMO_PRODUCT)
    products = [product for _customer, product, _quantity in ROWS]
    for product in products:
        odoo_app.product_id(product)
    customers = [f"Scale Customer {index:03d}" for index in range(1, ROW_COUNT + 1)]
    for name in customers:
        if not odoo_app.search("res.partner", [("name", "=", name)], limit=1):
            odoo_app.create("res.partner", {"name": name, "is_company": True})
    _note("customers ready")

    # 2. Record and compile exactly as the quotation golden does.
    record_new_quote(harness)
    columns = recorded_columns(harness)
    customer_column = column_for(columns, "customer", "partner")
    outcome = harness.compile_via_popup(
        "New sales quotation", record_key=customer_column, park_fields=[]
    )
    recipe = outcome["recipe"]
    assert recipe is not None, outcome
    _note(f"compiled: {[step.get('id') for step in recipe['steps']]}")

    options = {product: f"[{odoo_app.product_code(product)}] {product}" for product in products}
    fields = recipe["meta"]["input_schema"]["fields"]
    rows = [
        rows_for(
            fields,
            customer=customer,
            product=products[index % len(products)],
            quantity=str(index % 9 + 1),
            product_option=options[products[index % len(products)]],
        )
        for index, customer in enumerate(customers)
    ]

    # 3. The run, timed.
    run_id = harness.make_run(rows, recipe=recipe, key_column=customer_column)
    started = time.monotonic()
    _note(f"run {run_id} created")
    try:
        records = harness.settle_run(run_id, timeout=RUN_SECONDS, max_seconds=RUN_SECONDS - 60)
    finally:
        elapsed = time.monotonic() - started
        _note(f"run settled or stopped after {elapsed:.0f}s: {harness.describe(run_id)[:2000]}")

    # 4. What happened, in numbers, before anything is asserted.
    states = Counter(record["state"] for record in records)
    reasons = Counter(
        str(record.get("reason") or "")[:160]
        for record in records
        if record["state"] not in {"verified", "skipped"}
    )
    in_odoo = sum(1 for name in customers if odoo_app.orders_by_partner(odoo_app.partner_id(name)))
    detail = harness.api_get(f"/api/v1/runs/{run_id}")
    summary: dict[str, Any] = {
        "rows": ROW_COUNT,
        "seconds": round(elapsed),
        "seconds_per_row": round(elapsed / max(ROW_COUNT, 1), 1),
        "states": dict(states),
        "not_done_reasons": dict(reasons.most_common(20)),
        "customers_with_an_order_in_odoo": in_odoo,
        "run_status": detail.get("status"),
        "pause_reason": detail.get("pause_reason"),
        "attempts_over_one": sum(1 for record in records if int(record.get("attempts") or 0) > 1),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _note("summary: " + json.dumps(summary))

    assert states["verified"] == ROW_COUNT, summary
    assert in_odoo == ROW_COUNT, summary
