"""PL-063 part 2 and PL-079: record and replay a product price update on real Odoo 18.

The chain a person uses: Sales -> Products -> search for one product by its
internal reference -> open it -> change **Sales Price** -> Save. The sheet has
`sku` and `new_price` columns; the record key is the sku, and the idempotency key
is the record key **and the price it writes** (``{{row.sku | lower}}:{{row.new_price}}``,
``internal/docs/RECIPE_SPEC.md``): the same change applies once, while a later change
to the same product is new work.

The search here is a real search (the search view, Enter applied). PL-077
tells the two searched shapes apart: this recording opens the result the search
found and commits on that record's own page, so it is a find-then-update, and
the popup words its rule "Skip a row when the search finds nothing" -- the
product the search finds is the one to change, so that rule never skips a row
this recording would have updated. The golden leaves the rule as the popup
offers it and asserts that wording.

PL-079 adds the second half: an Odoo form keeps Save hidden while it is clean, so
a row whose values the page already shows has nothing to save. The recipe carries
a compiled branch, right before the commit click, that ends such a row `skipped`
with "Already had these values, so nothing was changed." -- the recipe's own step,
never a runner special case (D-017).

The requirement, in three runs of one compiled recipe:

1. three rows, each product's `list_price` equals its row's price, all `verified`;
2. the same sheet in the same tenant: all three `skipped` as already committed;
3. one row changed to a new price and the other two exactly what Odoo already has,
   in a fresh tenant so no committed keys apply: the changed row `verified`, the
   other two `skipped` by the new branch.

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

# PL-079 run 3: one row gets a new price, and the other two ask for exactly the price Odoo already
# holds -- in a fresh tenant, so nothing is skipped as already committed and every row is the
# recipe's own decision. The branch's sentence is pinned here, from the golden's side.
CHANGED_PRICE = "779.00"
ALREADY_REASON = "Already had these values, so nothing was changed."


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
    price = page.locator("div[name='list_price'] input").first
    price.wait_for(state="visible", timeout=20_000)
    price.click()
    price.fill("")
    price.press_sequentially(DEMO_PRICE, delay=30)
    page.wait_for_timeout(1500)
    last_action_seq = max(
        (event.get("seq", 0) for event in harness.recording_events() if event.get("step")),
        default=0,
    )
    page.locator("button.o_form_button_save").first.click()
    page.wait_for_timeout(4000)

    # The commit's evidence arrives from the page the save stayed on: an update-in-place produces
    # no navigation (the product page's URL does not move) and no toast, so the recorder reports
    # the Save control's own disappearance (PL-075) as `element_absent` plus the record page's URL.
    observation = harness.wait_for_observation_after(last_action_seq)
    _trace(
        f"save evidence recorded: {json.dumps(observation, default=str)} "
        f"(events after the save: "
        + json.dumps(
            [
                {
                    "seq": event.get("seq"),
                    "kind": event.get("kind"),
                    "step": (event.get("step") or {}).get("id"),
                    "observation": event.get("observation"),
                }
                for event in harness.recording_events()
                if int(event.get("seq", 0)) > last_action_seq
            ],
        )
        + ")"
    )
    harness.stop_recording()


# ------------------------------------------------------------------- the compile


# PL-077/PL-106: the popup words the search rule for the shape the compiler reports. An update
# skips a row whose search found nothing; it never skips one it found. The golden reads that
# sentence.
PARK_WHEN_NOTHING_FOUND = "Skip a row when the search finds nothing"


def compile_update_recipe(harness, name: str, record_key: str | None) -> dict[str, Any]:
    """Compile through the popup's own form, leaving the search rule as the popup words it.

    PL-077: the popup used to propose the skip-if-the-search-finds-it branch, which for an update
    would end every row `skipped` -- the product the search finds is the one the row is about to
    update -- so this golden unticked it. The compiler now tells the two shapes apart, and the
    popup words the same rule as "Skip a row when the search finds nothing" for an update; the
    form is left exactly as the popup offers it, and the wording is asserted below.
    """
    popup = harness.popup
    popup.evaluate("(v) => { document.getElementById('hub').value = v; }", harness.api_base)
    popup.fill("#make-name", name)
    if record_key is not None:
        popup.select_option("#make-key", value=record_key)
    duplicate = popup.locator("#make-duplicate")
    proposed = duplicate.is_checked() if duplicate.count() else None
    _trace(f"popup proposes the search rule: {proposed}")
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
    warnings = popup.evaluate("() => document.getElementById('make-warnings')?.textContent || ''")
    # PL-077: the rule's own sentence, as the form words it once the compiler has answered.
    rule_words = popup.evaluate(
        "() => document.getElementById('make-duplicate')"
        "?.closest('label')?.querySelector('.field__label')?.textContent || ''"
    )
    _trace(f"popup rule wording: {rule_words!r}")
    _trace(f"popup result: {result!r} warnings: {warnings!r}")
    stored = popup.evaluate(
        "async () => { const s = await chrome.storage.session.get("
        "['processlens:executor-recipe', 'processlens:executor-row']);"
        " return { recipe: s['processlens:executor-recipe'] ?? null }; }"
    )
    compiled = "is ready" in str(result)
    return {
        "recipe": stored.get("recipe") if compiled else None,
        "duplicate_proposed": proposed,
        "rule_words": rule_words,
        "result": result,
        "warnings": warnings,
    }


def rows_for(
    fields: dict[str, Any], rows: tuple[tuple[str, str], ...] | list[tuple[str, str]]
) -> list[dict[str, str]]:
    """One row per sheet row, valued for *every* field the compiled schema carries.

    The compiled schema, not the popup's labels, is the source of truth: what a row must fill is
    exactly what the recipe's steps read. Each field's ``example`` is the value the demonstration
    typed for it, so the row's answer is that value's stand-in: the demonstration's sku becomes the
    row's sku, the demonstration's price becomes the row's price. A field whose example matches
    nothing this golden knows about fails here instead of being silently left blank.
    """
    built: list[dict[str, str]] = []
    for sku, price in rows:
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
        built.append(row)
    return built


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

    # 3. Compile through the popup's own form, once, the way the quotation golden does: the
    #    record key is read from the columns the form offers (their labels name what the
    #    demonstration typed -- the search box and the price field), and the row builder maps
    #    every column by its compiled example afterwards. The sku is the record key: each row
    #    names its own product's internal reference.
    columns = [(column["label"], column["value"]) for column in harness.make_form_columns()]
    _trace(f"columns: {columns}")
    if os.environ.get("PROCESSLENS_E2E_DUMP_RECIPE") == "1":
        print("RECORDING:", json.dumps(events, indent=2, sort_keys=True))
        print("COLUMNS:", json.dumps(columns, indent=2))

    def column_for(*needles: str) -> str:
        """The first column whose label mentions any needle, case-insensitively."""
        for needle in needles:
            for label, value in columns:
                if needle.lower() in label.lower():
                    return value
        raise AssertionError(
            f"no recorded column mentions {needles!r}; the form offers {columns!r}"
        )

    sku_column = column_for("search", "sku", "reference")
    outcome = compile_update_recipe(harness, "Update product price", sku_column)
    assert outcome["recipe"] is not None, (
        "the Make recipe form could not compile the recording: "
        f"result={outcome['result']!r} warnings={outcome['warnings']!r} events={json.dumps(events)}"
    )
    recipe = outcome["recipe"]
    # PL-077/PL-106: an update skips a row when its search finds nothing, and the form says so.
    assert outcome["rule_words"] == PARK_WHEN_NOTHING_FOUND, (
        f"the popup words the search rule as {outcome['rule_words']!r}, not "
        f"{PARK_WHEN_NOTHING_FOUND!r} ({outcome['warnings']!r})"
    )
    _trace(
        f"compile ok: steps={[step.get('id') for step in recipe['steps']]} "
        f"idempotency_key={(recipe.get('meta') or {}).get('idempotency_key')!r} "
        f"duplicate_proposed={outcome['duplicate_proposed']}"
    )
    if os.environ.get("PROCESSLENS_E2E_DUMP_RECIPE") == "1":
        print("COMPILED RECIPE:", json.dumps(recipe, indent=2, sort_keys=True))
    fields = recipe["meta"]["input_schema"]["fields"]

    def column_of(example: str) -> str | None:
        for column, spec in fields.items():
            if str((spec or {}).get("example", "")) == example:
                return column
        return None

    price_column = column_of(DEMO_PRICE)
    assert price_column is not None, (
        f"the compiled schema does not carry the new price ({DEMO_PRICE!r}) as a column; "
        f"fields: {sorted(fields)}"
    )

    # 4. Replay the three rows.
    fields = recipe["meta"]["input_schema"]["fields"]
    rows = rows_for(fields, ROWS)
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

    # 6. PL-079 run 2: the same sheet, in the same tenant. Every key was committed by run 1, so the
    #    ledger skips all three before the browser is touched -- the idempotency the key is for.
    #    A tenant's committed keys are its own, so the run is made in run 1's tenant on purpose: a
    #    fresh tenant (the harness default) would have none to skip.
    import psycopg

    with psycopg.connect(harness.dsn) as conn:
        first_tenant = conn.execute(
            "SELECT tenant_id FROM runs WHERE id = %s", (run_id,)
        ).fetchone()[0]
    run2 = harness.make_run(rows, recipe=recipe, key_column=sku_column, tenant_id=str(first_tenant))
    try:
        records2 = harness.settle_run(run2, timeout=700, max_seconds=680)
    except Exception:
        _trace(f"settle_run FAILED on run {run2}: {harness.describe(run2)}")
        raise
    detail2 = detail_for(harness, records2)
    _trace("second run settled: " + detail2)
    counts2 = Counter(record["state"] for record in records2)
    assert counts2["skipped"] == 3, f"the second run was not skipped outright: {detail2}"
    for record in records2:
        assert "already committed" in str(record.get("reason") or ""), detail2
    for sku, price in ROWS:
        assert odoo_app.product_list_price(sku) == float(price), (
            f"the second run changed {sku!r}'s price ({detail2})"
        )
    _trace(f"second run states: {dict(counts2)}")

    # 7. PL-079 run 3: one row changed to a new price and the other two exactly the price Odoo
    #    already holds, in a fresh tenant so no committed key applies to any row. The changed row
    #    must be written and verified. The other two have nothing to write: Odoo keeps Save hidden
    #    on a clean form, so the recipe's own branch -- compiled right before the commit click, and
    #    only for an update whose proof is a disappearing control -- finds the control absent and
    #    ends them `skipped` with its own sentence. Which of the two outcomes the unchanged rows get
    #    is Odoo's answer to "does a same-value fill dirty the form?", and the trace keeps it.
    run3_rows = rows_for(fields, ((ROWS[0][0], CHANGED_PRICE), *ROWS[1:]))
    _trace(f"third run rows: {json.dumps(run3_rows)}")
    run3 = harness.make_run(run3_rows, recipe=recipe, key_column=sku_column)
    _trace(f"run {run3} created")
    try:
        records3 = harness.settle_run(run3, timeout=700, max_seconds=680)
    except Exception:
        _trace(f"settle_run FAILED on run {run3}: {harness.describe(run3)}")
        raise
    detail3 = detail_for(harness, records3)
    _trace("third run settled: " + detail3)
    counts3 = Counter(record["state"] for record in records3)
    by_key = {record["record_key"]: record for record in records3}
    changed = by_key.get(ROWS[0][0].lower())
    assert changed is not None, detail3
    assert changed["state"] == "verified", (
        f"the row with a new price was not written and verified: {detail3}"
    )
    for sku, _price in ROWS[1:]:
        record = by_key.get(sku.lower())
        assert record is not None, detail3
        assert record["state"] == "skipped", (
            f"the row that already matched was not skipped by the recipe's branch: {detail3}"
        )
        assert record.get("reason") == ALREADY_REASON, (
            f"the skipped row's reason is not the branch's own sentence: {detail3}"
        )
    assert odoo_app.product_list_price(ROWS[0][0]) == float(CHANGED_PRICE), detail3
    for sku, price in ROWS[1:]:
        assert odoo_app.product_list_price(sku) == float(price), (
            f"the third run changed {sku!r}'s price, which it had no reason to ({detail3})"
        )
    _trace(f"third run states: {dict(counts3)}")

    # 8. The update workflow's idempotency key (PL-063's finding, PL-079's fix). The key is the
    #    record key's own template, then ':', then every column the update's fills write -- here the
    #    sku and the new price -- so the same change applies once while a later change to the same
    #    product is a new key.
    meta = recipe.get("meta") if isinstance(recipe.get("meta"), dict) else {}
    idempotency_key = str(meta.get("idempotency_key") or "")
    _trace(f"idempotency_key: {idempotency_key!r} (sku {sku_column!r}, price {price_column!r})")
    assert idempotency_key == f"{meta.get('record_key')}:{{{{row.{price_column}}}}}", (
        f"the compiled idempotency_key {idempotency_key!r} is not the record key "
        f"{meta.get('record_key')!r} followed by the price column {price_column!r}"
    )
    assert sku_column in idempotency_key and price_column in idempotency_key, (
        f"the compiled idempotency_key {idempotency_key!r} does not carry both columns "
        f"({sku_column!r}, {price_column!r})"
    )
