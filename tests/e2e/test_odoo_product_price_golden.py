"""PL-063 part 2: record and replay a product price update on real Odoo 18.

The chain a person uses: Sales -> Products -> search for one product by its
internal reference -> open it -> change **Sales Price** -> Save. The sheet has
`sku` and `new_price` columns; the record key is the sku. This is the brief's
"update workflow" case, where the idempotency key carries the new value
(``internal/docs/RECIPE_SPEC.md``: ``{{row.sku}}:{{row.new_price}}``) so the
same change applies once while a later change to the same record is still a
new key.

The search here is a real search (the search view, Enter applied), which is
why the duplicate-search heuristic has something to read. The popup proposes
its skip-if-the-search-finds-it branch when a search is detected; for an
update the branch would skip every row -- the product always exists -- so the
golden unticks it, which is what a person updating prices would do, and the
trace records that the popup proposed it.

The requirement: three rows, each product's `list_price` equals its row's
price, all rows `verified`, and a second run of the same sheet changes nothing.
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

TRACE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "pl063" / "price-golden-trace.log"


def _trace(message: str) -> None:
    """Append one line of where-the-test-is to the trace file."""
    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRACE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.real_apps,
]

# One demonstration's values, synthetic on purpose: they become the sample row.
DEMO_SKU = "PL-CHR-2001"
DEMO_PRICE = "349.00"

# The three rows the batch replays: three seeded products, each with a new price.
ROWS = (
    ("PL-DESK-1002", "729.00"),
    ("PL-CHR-2002", "949.00"),
    ("PL-MON-3001", "459.00"),
)


# --------------------------------------------------------------- the demonstration


def record_price_change(harness) -> None:
    """Teach "find a product by its internal reference and change its sales price"."""
    page = harness.page
    page.goto(f"{harness.crm_url}/odoo/products", wait_until="domcontentloaded")
    # The products menu opens on the kanban; a person updating prices reads a list, and the
    # switch is part of the task here (find one product among many). The list's rows are what
    # the search lands on.
    page.locator("button.o_switch_view.o_list").first.click(timeout=20_000)
    page.locator("tr.o_data_row").first.wait_for(state="visible", timeout=20_000)
    page.wait_for_timeout(1500)

    harness.start_recording()

    # The search: Odoo's search view takes the whole query and Enter applies it. This is a real
    # search of the destination, which is why the duplicate heuristic has something to read.
    box = page.locator("input.o_searchview_input").first
    box.click()
    box.press_sequentially(DEMO_SKU, delay=30)
    page.wait_for_timeout(600)
    page.keyboard.press("Enter")
    page.wait_for_timeout(3000)

    # Open the product the search found: its row in the filtered list.
    row = page.locator("tr.o_data_row").first
    row.wait_for(state="visible", timeout=20_000)
    row.click(timeout=10_000)
    page.wait_for_url(re.compile(r".*/odoo/products/\d+.*"), timeout=30_000)
    page.wait_for_timeout(2000)

    # Change Sales Price and save. The form is replaced by the saved product's page.
    last_action_seq = max(
        (event.get("seq", 0) for event in harness.recording_events() if event.get("step")),
        default=0,
    )
    price = page.locator("div[name='list_price'] input").first
    price.wait_for(state="visible", timeout=20_000)
    price.click()
    price.fill("")
    price.press_sequentially(DEMO_PRICE, delay=30)
    page.wait_for_timeout(1500)
    page.locator("button.o_form_button_save").first.click()
    page.wait_for_timeout(3000)

    # The commit's evidence arrives from the page the save landed on, and the worker attaches it
    # while the recording is still on.
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


# ------------------------------------------------------------------- the compile


def compile_update_recipe(harness, name: str, record_key: str | None) -> dict[str, Any]:
    """Compile through the popup's own form, with the duplicate branch unticked.

    The popup proposes the skip-if-the-search-finds-it branch when the recording holds a search;
    for an update workflow that branch would end every row `skipped` -- the product the search
    finds is the one the row is about to update -- so the person unticks it. Whether the popup
    proposed it is read before the untick and left in the trace.
    """
    popup = harness.popup
    popup.evaluate("(v) => { document.getElementById('hub').value = v; }", harness.api_base)
    popup.fill("#make-name", name)
    if record_key is not None:
        popup.select_option("#make-key", value=record_key)
    duplicate = popup.locator("#make-duplicate")
    proposed = duplicate.is_checked() if duplicate.count() else None
    _trace(f"popup proposed the duplicate branch: {proposed}")
    if proposed:
        popup.uncheck("#make-duplicate")
    # Tick no park boxes (the form's own default) and clear the two session keys first: they are
    # shared across tests, and a rejected compile stores nothing.
    popup.evaluate(
        "(wanted) => { const boxes = document.querySelectorAll("
        "'#make-park-fields input[type=\"checkbox\"]');"
        " for (const box of boxes) { const column = box.getAttribute('data-column') || '';"
        " const on = wanted.includes(column);"
        " if (box.checked !== on) { box.checked = on;"
        " box.dispatchEvent(new Event('change', { bubbles: true })); } } }",
        [],
    )
    popup.evaluate(
        "async () => { await chrome.storage.session.remove("
        "['processlens:executor-recipe', 'processlens:executor-row']); }"
    )
    popup.click("#make-go")
    popup.wait_for_function(
        "() => { const t = document.getElementById('make-result')?.textContent || '';"
        " return t.includes('is ready') || t.includes('Could not'); }",
        timeout=25_000,
    )
    result = popup.evaluate("() => document.getElementById('make-result')?.textContent || ''")
    stored = popup.evaluate(
        "async () => { const s = await chrome.storage.session.get("
        "['processlens:executor-recipe', 'processlens:executor-row']);"
        " return { recipe: s['processlens:executor-recipe'] ?? null }; }"
    )
    compiled = "is ready" in str(result)
    return {"recipe": stored.get("recipe") if compiled else None, "duplicate_proposed": proposed}


def detail_for(harness, records: list[dict[str, Any]]) -> str:
    """Each record with its own steps, in order, so a failure says where the run went."""
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


def test_odoo_product_price_golden(odoo_harness, odoo_app) -> None:
    """Record once on real Odoo, compile through the popup, replay three rows, verify in Odoo."""
    harness = odoo_harness
    _trace("=== price golden: start ===")
    # 1. What the demonstration and the rows will look for. Each row's product must exist by its
    #    internal reference, so the search finds exactly it.
    odoo_app.product_list_price(DEMO_SKU)
    for sku, _price in ROWS:
        odoo_app.product_list_price(sku)
    _trace("seed ok")

    # 2. Record the demonstration through the real extension.
    try:
        record_price_change(harness)
    except Exception:
        _trace(
            "record_price_change FAILED; events so far: "
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

    # 3. Compile through the popup's own form, twice on purpose: the first compile tells this
    #    golden what the columns are (the schema carries each column's example), and the second
    #    compiles the recipe the run uses, with the sku as the record key. The sku is the record
    #    key: each row names its own product's internal reference.
    columns = [(column["label"], column["value"]) for column in harness.make_form_columns()]
    _trace(f"columns: {columns}")
    if os.environ.get("PROCESSLENS_E2E_DUMP_RECIPE") == "1":
        print("RECORDING:", json.dumps(events, indent=2, sort_keys=True))
        print("COLUMNS:", json.dumps(columns, indent=2))
    outcome = compile_update_recipe(harness, "Update product price", None)
    assert outcome["recipe"] is not None, (
        f"the Make recipe form could not compile the recording: events={json.dumps(events)}"
    )
    fields = outcome["recipe"]["meta"]["input_schema"]["fields"]

    def column_of(example: str) -> str | None:
        for column, spec in fields.items():
            if str((spec or {}).get("example", "")) == example:
                return column
        return None

    sku_column = column_of(DEMO_SKU)
    price_column = column_of(DEMO_PRICE)
    assert sku_column is not None and price_column is not None, (
        f"the compiled schema does not carry the sku ({DEMO_SKU!r}) and the new price "
        f"({DEMO_PRICE!r}) as columns; fields: {sorted(fields)}"
    )
    outcome = compile_update_recipe(harness, "Update product price", sku_column)
    assert outcome["recipe"] is not None, (
        "the Make recipe form could not compile the recording with the sku as the key: "
        f"events={json.dumps(events)}"
    )
    recipe = outcome["recipe"]
    _trace(
        f"compile ok: steps={[step.get('id') for step in recipe['steps']]} "
        f"idempotency_key={recipe.get('idempotency_key')!r} "
        f"duplicate_proposed={outcome['duplicate_proposed']}"
    )
    if os.environ.get("PROCESSLENS_E2E_DUMP_RECIPE") == "1":
        print("COMPILED RECIPE:", json.dumps(recipe, indent=2, sort_keys=True))

    # 4. Replay the three rows.
    fields = recipe["meta"]["input_schema"]["fields"]
    rows = []
    for sku, price in ROWS:
        row: dict[str, str] = {}
        for column, spec in fields.items():
            sample = str((spec or {}).get("example", ""))
            if sample == DEMO_SKU:
                row[column] = sku
            elif sample == DEMO_PRICE:
                row[column] = price
            else:
                raise AssertionError(
                    f"the compiled schema carries a field this golden does not know how to fill: "
                    f"{column!r} (example {sample!r}; all fields: {sorted(fields)})"
                )
        rows.append(row)
    _trace(f"rows: {json.dumps(rows)}")
    run_id = harness.make_run(rows, recipe=recipe, key_column=sku_column)
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

    # 5. Odoo's own truth: each product's list_price equals its row's price.
    for sku, price in ROWS:
        got = odoo_app.product_list_price(sku)
        assert got == float(price), f"{sku!r} list_price is {got}, expected {price} ({detail})"

    # 6. A second run of the same sheet changes nothing: the ledger's own idempotency. The rows
    #    end skipped (already-committed keys) or verified without a second write, whichever the
    #    ledger's rule gives; what actually happened goes in the trace and the report.
    run2 = harness.make_run(rows, recipe=recipe, key_column=sku_column)
    try:
        records2 = harness.settle_run(run2, timeout=700, max_seconds=680)
    except Exception:
        _trace(f"settle_run FAILED on run {run2}: {harness.describe(run2)}")
        raise
    detail2 = detail_for(harness, records2)
    _trace("second run settled: " + detail2)
    counts2 = Counter(record["state"] for record in records2)
    assert all(state in {"skipped", "verified"} for state in counts2), (
        f"the second run wrote again: {detail2}"
    )
    for sku, price in ROWS:
        assert odoo_app.product_list_price(sku) == float(price), (
            f"the second run changed {sku!r}'s price ({detail2})"
        )
    _trace(f"second run states: {dict(counts2)}")

    # 7. The update workflow's idempotency key carries BOTH the sku and the new price
    #    (RECIPE_SPEC: {{row.sku}}:{{row.new_price}}), so the same change applies once while a
    #    later change to the same product is still a new key. A key of the record key alone
    #    would refuse every later price change to the same product.
    idempotency_key = str(recipe.get("idempotency_key") or "")
    assert sku_column in idempotency_key and price_column in idempotency_key, (
        f"the compiled idempotency_key {idempotency_key!r} does not carry both the sku column "
        f"{sku_column!r} and the new-price column {price_column!r}: this is the finding"
    )
