"""PL-046: record and replay a sales quotation on real Odoo 18.

The chain a person uses, on software nobody here wrote: Sales -> Quotations ->
New -> Customer (picked from a many2one autocomplete) -> Add a product ->
Product (picked from the editable row's own autocomplete) -> Quantity typed ->
Save. The order line is added in an editable list, which no other golden
exercises: this ticket finds out whether list-row editing records and replays,
and reports exactly where it does not.

**It does not replay (PL-046's finding, marked `xfail(strict=True)`).** The
demonstration records and compiles; the replay parks every row before any
quotation is written. The compiled defects, in the order a replay meets them:

1. The editable row's fields have no accessible names, only placeholders, so
   the recorder can offer no rung that resolves to exactly one element. The
   first casualty is the product field's own click ("Click Search a product"):
   its role rung (the placeholder "Search a product") matches nothing and its
   css rung ``input.o-autocomplete--input.o_input`` matches two (the customer's
   autocomplete and the row's), so the ladder refuses it -- every row parks
   with "the click target could not be resolved: no rung of the target ladder
   resolved to exactly one element" before any line is added. The quantity
   fill ("Fill input", css rung ``input.o_input``, many matches) sits right
   behind it and would refuse the same way.
2. The product autocomplete is recorded as "Search a product", and the
   compiler's duplicate check reads any fill followed by a click naming
   "search" as a search, so every row carries a skip-as-duplicate branch keyed
   on the product name.
3. The save's evidence is the demonstration's own quotation number
   (``text_visible: "S00001"``), recorded verbatim into the commit's
   ``assert_after``; no replay row's save can show it.

When the recorder, compiler or runner learns any of this, the strict xfail will
XPASS and fail -- remove the marker and the assertions below are the ticket's
full requirement: three rows, each one `sale.order` for its customer with
exactly one line of its product at its quantity, all `verified`.

The app is reset by `infra/test-apps/reset.py odoo` before the test runs
(``odoo_reset``), so the golden can be run again.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

# Where this test leaves its trail. The test is `xfail(strict=True)`, and pytest shows nothing
# of an xfailed run -- not its prints, not its captured output -- so every phase boundary and
# every run diagnostic is appended here, under the repo's gitignored `.cache/`.
TRACE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "pl046" / "quote-golden-trace.log"


def _trace(message: str) -> None:
    """Append one line of where-the-test-is to the trace file."""
    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRACE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.real_apps,
    pytest.mark.xfail(
        strict=True,
        reason=(
            "PL-046: an editable order-line row's fields record with no accessible name, so "
            "the compiled recipe cannot replay them. Every row parks at the product field's "
            "own click ('Click Search a product'): the role rung is the field's placeholder "
            "and matches nothing, and the css rung 'input.o-autocomplete--input.o_input' "
            "matches two autocomplete inputs, so the ladder refuses it -- 'the click target "
            "could not be resolved: no rung of the target ladder resolved to exactly one "
            "element'. The quantity fill ('Fill input', css rung 'input.o_input') sits behind "
            "it and refuses the same way; behind those, the product autocomplete trips the "
            "compiler's duplicate-check branch, and the commit asserts the demonstration's "
            "own quotation number ('S00001'), which no replay row can show."
        ),
    ),
]

# One demonstration's values, synthetic on purpose: they become the sample row.
DEMO_CUSTOMER = "Summit Climbing Gym"
DEMO_PRODUCT = "Mesh Task Chair"
DEMO_QTY = "3"

# The three rows the batch replays: a different customer, product and quantity each. Every
# customer and product is one the seeder created, so each autocomplete offers the option the
# row asks for, exactly as the demonstration saw its own.
ROWS = (
    ("Ridgeline Outfitters", "Compact Mechanical Keyboard", "5"),
    ("Harbourview Dental", "USB-C Dock 10-in-1", "2"),
    ("Pinewood Preschool", "Oak Standing Desk", "4"),
)


# --------------------------------------------------------------- the demonstration


def record_new_quote(harness) -> None:
    """Teach "new sales quotation" on Odoo, exactly as the ticket's demonstration describes it."""
    page = harness.page
    page.goto(f"{harness.crm_url}/odoo/sales", wait_until="domcontentloaded")
    page.wait_for_load_state("load")
    page.wait_for_timeout(4000)
    page.locator("button.o_list_button_add").first.wait_for(state="visible", timeout=20_000)
    page.wait_for_timeout(1500)

    harness.start_recording()

    # New: the quotations list's New opens the full sale.order form.
    page.locator("button.o_list_button_add").first.click(timeout=20_000)
    page.wait_for_url(re.compile(r".*/odoo/sales/new.*"), timeout=30_000)
    page.wait_for_timeout(3000)

    # Customer: a many2one autocomplete on the form. The full seeded name is typed, so the option
    # the click accepts carries the very text the row will vary.
    customer = page.locator("div[name='partner_id'] input").first
    customer.wait_for(state="visible", timeout=20_000)
    customer.click()
    customer.fill("")
    customer.press_sequentially(DEMO_CUSTOMER, delay=40)
    page.wait_for_timeout(2000)
    option = page.locator("a[role='option'][id^='partner_id_0_0_']", has_text=DEMO_CUSTOMER).first
    option.wait_for(state="visible", timeout=20_000)
    option.click()
    page.wait_for_timeout(1500)

    # Add a product: the editable order-line list's own add control, a plain link that Odoo
    # renders as a button. It opens a new empty row with the product field focused.
    page.locator("a:has-text('Add a product')").first.click(timeout=10_000)
    page.wait_for_timeout(2000)

    # Product: a many2one autocomplete inside the editable row. Its options are named
    # "[CODE] Name", so the typed name alone is what the row varies; the code stays a literal.
    product = page.locator("div[name='product_template_id'] input").first
    product.wait_for(state="visible", timeout=20_000)
    product.click()
    product.press_sequentially(DEMO_PRODUCT, delay=40)
    page.wait_for_timeout(2000)
    product_option = page.locator(
        "a[role='option'][id^='autocomplete_0_']", has_text=DEMO_PRODUCT
    ).first
    product_option.wait_for(state="visible", timeout=20_000)
    product_option.click()
    page.wait_for_timeout(2000)

    # Quantity: a float field of the same row, typed after the product is set.
    qty = page.locator("div[name='product_uom_qty'] input").first
    qty.wait_for(state="visible", timeout=20_000)
    qty.click()
    qty.fill("")
    qty.press_sequentially(DEMO_QTY, delay=30)
    page.wait_for_timeout(1500)

    # Save. The form is replaced by the saved quotation's page, in the same document.
    last_action_seq = max(
        (event.get("seq", 0) for event in harness.recording_events() if event.get("step")),
        default=0,
    )
    page.locator("button.o_form_button_save").first.click()
    page.wait_for_url(re.compile(r".*/odoo/sales/\d+.*"), timeout=30_000)
    page.wait_for_timeout(1000)

    # The commit's evidence arrives from the page the save landed on, and the worker attaches it
    # while the recording is still on. Wait for evidence about the *save*: an earlier action
    # already carries an observation, so "some event has one" would answer at once.
    assert harness.wait_for_observation_after(last_action_seq), (
        "the save's own evidence (a confirmation or the navigation) was never recorded: "
        + json.dumps(
            [
                {
                    "seq": event.get("seq"),
                    "kind": event.get("kind"),
                    "step": (event.get("step") or {}).get("id"),
                    "observation": event.get("observation"),
                    "url": event.get("url"),
                }
                for event in harness.recording_events()
            ],
            indent=1,
        )
    )
    harness.stop_recording()


# ------------------------------------------------------------------- the rows


def recorded_columns(harness) -> list[tuple[str, str]]:
    """The recording's input columns as (label, column) pairs, in the form's own order.

    The form lists one option per column; the golden reads them back instead of pinning column
    names, and fills every one of them. A list of pairs, not a dict: two columns can share one
    label (the product autocomplete may produce one column for the typed name and another for
    the option that replaced it), and a dict keyed by label silently drops one of them.
    """
    return [(column["label"], column["value"]) for column in harness.make_form_columns()]


def column_for(columns: list[tuple[str, str]], *needles: str) -> str:
    """The first column whose label mentions any needle, case-insensitively."""
    for needle in needles:
        for label, value in columns:
            if needle.lower() in label.lower():
                return value
    raise AssertionError(f"no recorded column mentions {needles!r}; the form offers {columns!r}")


def rows_for(
    fields: dict[str, Any],
    *,
    customer: str,
    product: str,
    quantity: str,
    product_option: str,
) -> dict[str, str]:
    """One row, valued for *every* field the compiled schema carries.

    The compiled schema, not the popup's labels, is the source of truth here: what a row must
    fill is exactly what the recipe's steps read. Each field's ``example`` is the value the
    demonstration typed or picked for it, so the row's answer is that value's stand-in: the
    demonstration's customer becomes the row's customer, the typed product name becomes the
    row's product, the accepted option ("[CODE] Name") becomes the row's "[CODE] Name", and
    the typed quantity becomes the row's quantity. A field whose example matches nothing this
    golden knows about fails here instead -- which is how a recorder that started inventing
    fields again would be caught rather than absorbed.
    """
    row: dict[str, str] = {}
    for column, spec in fields.items():
        sample = str((spec or {}).get("example", ""))
        if sample == DEMO_CUSTOMER:
            row[column] = customer
        elif sample == DEMO_PRODUCT:
            row[column] = product
        elif sample.startswith("[") and sample.endswith(DEMO_PRODUCT):
            row[column] = product_option
        elif sample == DEMO_QTY:
            row[column] = quantity
        else:
            raise AssertionError(
                f"the compiled schema carries a field this golden does not know how to fill: "
                f"{column!r} (example {sample!r}; all fields: {sorted(fields)})"
            )
    return row


def step_events_for(harness, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every step event of a run, with the record key it belongs to.

    A step event carries what a step observed and whether it failed; neither reaches the API, so
    a golden that needs to assert on one reads the ledger directly.
    """
    import psycopg

    keys = {str(record["id"]): record["record_key"] for record in records}
    events: list[dict[str, Any]] = []
    with psycopg.connect(harness.dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT run_record_id, step_id, outcome, observed, error FROM step_events "
            "WHERE run_record_id = ANY(%s::uuid[]) ORDER BY id",
            (list(keys),),
        )
        for row in cur.fetchall():
            events.append(
                {
                    "record_key": keys.get(str(row[0]), "?"),
                    "step_id": row[1],
                    "outcome": row[2],
                    "observed": row[3],
                    "error": row[4],
                }
            )
    return events


def detail_for(harness, records: list[dict[str, Any]]) -> str:
    """Each record with its own steps, in order, so a failure says where the run went.

    ``step_events`` is where a park or a skip names the step: the runner writes the step id, the
    rung and the executor's own error. Grouping them by record key is what turns a flat list into
    "this record did this, and stopped there".
    """
    import psycopg

    keys = {str(record["id"]): record["record_key"] for record in records}
    per_record: dict[str, list[str]] = {key: [] for key in keys.values()}
    with psycopg.connect(harness.dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT run_record_id, step_id, outcome, error, started_at FROM step_events "
            "WHERE run_record_id = ANY(%s::uuid[]) ORDER BY id",
            (list(keys),),
        )
        for row in cur.fetchall():
            error = row[3] if isinstance(row[3], str) else ""
            # `.get`: a diagnostic must never raise, and a record the API did not list still has
            # steps worth printing.
            key = keys.get(str(row[0]), f"<{str(row[0])[-6:]} >")
            at = row[4].strftime("%H:%M:%S") if hasattr(row[4], "strftime") else str(row[4])
            per_record.setdefault(key, []).append(
                f"{at} {row[1]}:{row[2]}"
                f"{'' if not error or error == 'null' else f'({error[:200]})'}"
            )
    lines = [
        f"{record['record_key']} = {record['state']}:{record.get('reason')}"
        f" attempts={record.get('attempts')} attempted_commit={record.get('commit_attempted')}"
        f" ref={record.get('destination_ref')} | "
        + " -> ".join(per_record.get(record["record_key"], []))
        for record in records
    ]
    run = harness.api_get(f"/api/v1/runs/{records[0]['run_id']}")
    lines.append(f"run status={run.get('status')} pause_reason={run.get('pause_reason')!r}")
    return "\n".join(lines)


# ------------------------------------------------------------------- the test


def test_odoo_new_quote_golden(odoo_harness, odoo_app) -> None:
    """Record once on real Odoo, compile through the popup, replay three rows, verify in Odoo."""
    harness = odoo_harness
    _trace("=== quote golden: start ===")
    # 1. What the demonstration and the rows will look for and pick. All must already exist, so
    #    every autocomplete offers the option it will be asked to take -- a demonstration that
    #    creates its own record would replay as a "Create ..." that is no longer offered.
    odoo_app.partner_id(DEMO_CUSTOMER)
    odoo_app.product_id(DEMO_PRODUCT)
    for customer, product, _quantity in ROWS:
        odoo_app.partner_id(customer)
        odoo_app.product_id(product)
    _trace("seed ok")

    # 2. Record the demonstration through the real extension.
    try:
        record_new_quote(harness)
    except Exception:
        _trace(
            "record_new_quote FAILED; events so far: "
            + json.dumps(harness.recording_events(), default=str)[:6000]
        )
        raise
    events = harness.recording_events()
    assert events, "the recorder captured nothing"
    assert any(harness.crm_url in (event.get("url") or "") for event in events), (
        f"the recording is not from Odoo ({harness.crm_url}): "
        f"{[event.get('url') for event in events]}"
    )
    _trace(f"record ok: {len(events)} events")

    # 3. Compile through the popup's own form. The customer is the record key: each row names its
    #    own customer, so the ledger's key distinguishes the three. Nothing is marked required,
    #    so no precheck rule parks a row before the browser is touched.
    columns = recorded_columns(harness)
    _trace(f"columns: {columns}")
    if os.environ.get("PROCESSLENS_E2E_DUMP_RECIPE") == "1":
        print("RECORDING:", json.dumps(events, indent=2, sort_keys=True))
        print("COLUMNS:", json.dumps(columns, indent=2))
    customer_column = column_for(columns, "customer", "partner")
    outcome = harness.compile_via_popup(
        "New sales quotation", record_key=customer_column, park_fields=[]
    )
    assert outcome["recipe"] is not None, (
        "the Make recipe form could not compile the recording: "
        f"result={outcome['result']!r} warnings={outcome['warnings']!r} events={json.dumps(events)}"
    )
    recipe = outcome["recipe"]
    _trace(
        f"compile ok: result={outcome['result']!r} warnings={outcome['warnings']!r} "
        f"steps={[step.get('id') for step in recipe['steps']]}"
    )
    if os.environ.get("PROCESSLENS_E2E_DUMP_RECIPE") == "1":
        print("COMPILED RECIPE:", json.dumps(recipe, indent=2, sort_keys=True))

    # The compiler's own default: preview the first commit for a person.
    assert recipe["meta"]["approval_policy"] == {"commit": "first_record"}, recipe["meta"]

    # 4. Replay the three rows. The compiled policy stands: `first_record` previews the first
    #    commit for a person, and the harness approves it as the operator page would.
    #
    #    The option the product autocomplete offers is named "[CODE] Name", so a row's answer to
    #    the accepted-option column is its own product's coded name, read from Odoo.
    product_options = {
        product: f"[{odoo_app.product_code(product)}] {product}" for _, product, _quantity in ROWS
    }
    rows = [
        rows_for(
            recipe["meta"]["input_schema"]["fields"],
            customer=customer,
            product=product,
            quantity=quantity,
            product_option=product_options[product],
        )
        for customer, product, quantity in ROWS
    ]
    _trace(f"rows: {json.dumps(rows)}")
    run_id = harness.make_run(rows, recipe=recipe, key_column=customer_column)
    _trace(f"run {run_id} created")
    try:
        records = harness.settle_run(run_id, timeout=700, max_seconds=680)
    except Exception:
        _trace(f"settle_run FAILED on run {run_id}: {harness.describe(run_id)}")
        raise
    _trace("settled: " + detail_for(harness, records))
    detail = detail_for(harness, records)

    counts = Counter(record["state"] for record in records)
    assert len(records) == 3, detail
    assert counts["verified"] == 3, detail
    assert counts["failed"] == 0, detail

    # 5. Odoo's own truth: each row produced exactly one sale.order for its customer, with
    #    exactly one line, of that product, at that quantity.
    for customer, product, quantity in ROWS:
        partner = odoo_app.partner_id(customer)
        orders = odoo_app.orders_by_partner(partner)
        assert len(orders) == 1, (
            f"{customer!r} has {len(orders)} sale.orders, expected exactly one ({detail})"
        )
        lines = odoo_app.order_lines(orders[0]["id"])
        assert len(lines) == 1, (
            f"the order for {customer!r} has {len(lines)} lines, expected exactly one ({detail})"
        )
        got_product = lines[0]["product_id"]
        assert got_product and got_product[0] == odoo_app.product_id(product), (
            f"the order for {customer!r} is not one line of {product!r}: {lines} ({detail})"
        )
        assert float(lines[0]["product_uom_qty"]) == float(quantity), (
            f"the order for {customer!r} is not {quantity} of {product!r}: {lines} ({detail})"
        )
