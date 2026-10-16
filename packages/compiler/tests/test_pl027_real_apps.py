"""PL-027: the compiler bugs the real apps exposed, each with the recording that showed it.

The golden tests drive Odoo 18 and Twenty CRM, so every assertion here is a
recording *as those pages actually produced it* -- the events are the ones the
extension captured on the real app, trimmed to the shapes that matter. Each test
names the page behaviour that made the old code wrong, because the value of this
file is that it fails again if the behaviour is undone.
"""

from __future__ import annotations

from typing import Any

from packages.compiler import CompileOptions, compile_recording

# The demo values: the sample row the popup sends, exactly as the form built it from the recording.
SAMPLE_ROW = {
    "search": "demo@example.test",
    "e_g_product_pricing": "Demo Lead",
    "phone": "+1 555 010 0001",
    "company_name": "Demo Co",
    "salesperson": "Priya",
    "salesperson_2": "Priya Raman",
    "tags": "Beginner",
}

ORIGIN = "http://127.0.0.1:8069"
CRM = f"{ORIGIN}/odoo/crm"
FORM = f"{ORIGIN}/odoo/crm/new"


def options(**overrides: Any) -> CompileOptions:
    base: dict[str, Any] = {
        "name": "New CRM lead",
        "when_to_use": "Create a lead in Odoo",
        "sample_row": dict(SAMPLE_ROW),
        "record_key_column": "e_g_product_pricing",
        "allowed_origins": [ORIGIN],
        # The popup's Make recipe form preselects the last recorded click as the commit, which is
        # how the Save becomes the step the ledger's write-ahead wraps.
        "commit_step_ids": ["click-2"],
    }
    base.update(overrides)
    return CompileOptions(**base)


def fill(seq: int, step_id: str, name: str, value: str, **extra: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "seq": seq,
        "kind": "fill",
        "url": FORM,
        "step": {
            "id": step_id,
            "action": "fill",
            "name": f"Fill {name}",
            "targets": [{"by": "role", "role": "textbox", "name": name}],
            "value": {"kind": "constant", "value": value},
            "clear_first": True,
        },
    }
    event.update(extra)
    return event


def click(seq: int, step_id: str, name: str, url: str = FORM, **extra: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "seq": seq,
        "kind": "click",
        "url": url,
        "step": {
            "id": step_id,
            "action": "click",
            "name": f"Click {name}",
            "targets": [{"by": "role", "role": "button", "name": name}],
        },
    }
    event.update(extra)
    return event


def search_recording(**overrides: Any) -> list[dict[str, Any]]:
    """The recording Odoo really produced: search the email, press Enter, create, save."""
    events = [
        {
            "seq": 1,
            "kind": "fill",
            "url": CRM,
            "step": {
                "id": "fill-1",
                "action": "fill",
                "name": "Fill Search...",
                "targets": [
                    {"by": "role", "role": "searchbox", "name": "Search..."},
                    {"by": "css", "selector": "input.o_searchview_input.o_input.d-print-none"},
                    {"by": "text_fuzzy", "value": "Search..."},
                ],
                "value": {"kind": "constant", "value": "demo@example.test"},
                "clear_first": True,
            },
        },
        {
            "seq": 2,
            "kind": "press",
            "url": CRM,
            "step": {
                "id": "press-1",
                "action": "press",
                "name": "Press Enter",
                "targets": [{"by": "role", "role": "searchbox", "name": "Search..."}],
                "key": "Enter",
            },
        },
        click(3, "click-1", "New", url=CRM),
        fill(4, "fill-2", "e.g. Product Pricing", "Demo Lead"),
        fill(5, "fill-3", "Email", "demo@example.test"),
        # The value Odoo's own form rendered before anyone touched the field.
        fill(
            6,
            "fill-4",
            "Expected Revenue",
            "0.00",
            warning="this field changed without any typing: the page set it",
            page_set=True,
        ),
        click(7, "click-2", "Save manually"),
        # The move Odoo made when the save landed: the record's own page.
        {"seq": 8, "kind": "navigate", "url": f"{ORIGIN}/odoo/crm/12", "step": None},
    ]
    for event in events:
        event.update(overrides.get("event", {}))
    return events


def test_a_search_run_with_enter_still_compiles_a_duplicate_branch() -> None:
    """Odoo's search view has no button: Enter runs it.

    A branch that is only recognised when a *click* runs the search means every Odoo workflow loses
    its duplicate check, and the batch replays straight into a second copy of a lead that exists.
    """
    result = compile_recording(search_recording(), options())
    branches = [step for step in result.recipe["steps"] if step.get("action") == "branch"]
    assert len(branches) == 1, result.recipe["steps"]
    assert branches[0]["then"][0]["outcome"] == "skipped"
    assert branches[0]["then"][0]["reason"] == "duplicate"


def test_the_duplicate_branch_reads_a_result_cell_not_the_whole_page() -> None:
    """Odoo draws the query it was given back onto the page as a search facet.

    So "the value is visible somewhere" is true whether or not the record exists, and a branch built
    on page-wide text skips every row -- including the three that should be created.
    """
    result = compile_recording(search_recording(), options())
    branch = next(step for step in result.recipe["steps"] if step.get("action") == "branch")
    assert branch["when"] == {
        "element_present": {
            "targets": [{"by": "role", "role": "cell", "name": "{{row.search | lower}}"}],
        }
    }
    assert "text_visible" not in branch["when"]


def test_the_branch_follows_the_column_the_destination_was_searched_by() -> None:
    """The ledger's key and the destination's duplicate check are two different things.

    This workflow is keyed on the lead's name -- which every row has, including the one with no
    email -- while Odoo is searched by email. The branch must read the email column, not the key.
    """
    result = compile_recording(search_recording(), options(record_key_column="e_g_product_pricing"))
    assert result.recipe["meta"]["record_key"] == "{{row.e_g_product_pricing | lower}}"
    branch = next(step for step in result.recipe["steps"] if step.get("action") == "branch")
    assert branch["when"]["element_present"]["targets"][0]["name"] == "{{row.search | lower}}"


def test_a_value_the_page_set_itself_is_not_a_step_or_a_column() -> None:
    """Odoo renders defaults (0.00 in Expected Revenue, the resolved salesperson) into the form.

    The state-diff recorder witnesses those values, and it says so on the envelope. Replaying them
    would type a computed default back into a field -- and, on Odoo, type "Administrator" over the
    salesperson the row actually asked for.
    """
    result = compile_recording(search_recording(), options())
    values = [
        step["value"]
        for step in result.recipe["steps"]
        if step.get("action") in ("fill", "select") and "value" in step
    ]
    columns = [value.get("column") for value in values if isinstance(value, dict)]
    assert "expected_revenue" not in columns, values
    assert any("the page set that value itself" in warning for warning in result.warnings), (
        result.warnings
    )


def test_a_fill_that_only_restates_the_suggestion_is_folded_away() -> None:
    """Odoo's many2one records the typed prefix, the option click, and then the value the field
    holds -- which is the option's own text.

    Typing that value a second time fought the widget: Odoo's inline completion had already left
    "Priya Rama" in the field, so the replay wrote "Priya RamaPriya Raman" and parked the record.
    The click already put the value there, so the fill is not a step; the column it came from stays
    in the sample row, so the option rung is still templated from it.
    """
    events = [
        fill(1, "fill-1", "Salesperson", "Priya"),
        {
            "seq": 2,
            "kind": "click",
            "url": FORM,
            "step": {
                "id": "click-1",
                "action": "click",
                "name": "Click Priya Raman",
                "targets": [
                    {"by": "role", "role": "option", "name": "Priya Raman"},
                    {"by": "css", "selector": "#user_id_0_0_0"},
                ],
            },
        },
        fill(3, "fill-2", "Salesperson", "Priya Raman"),
        click(4, "click-2", "Save manually"),
        # The move the save made: without evidence a commit cannot be compiled.
        {"seq": 5, "kind": "navigate", "url": f"{ORIGIN}/odoo/crm/21", "step": None},
    ]

    result = compile_recording(events, options(commit_step_ids=["click-2"]))

    rendered = [step for step in result.recipe["steps"] if step.get("action") in ("fill", "click")]
    assert [step["id"] for step in rendered] == ["fill-1", "click-1", "click-2"], rendered
    # The suggestion the clicked option names is still row-driven, so another row can name another
    # salesperson: the value the fold dropped is not lost, only the step that repeated it.
    assert rendered[1]["targets"][0]["name"] == "{{row.salesperson_2}}", rendered[1]
    assert any("folded the fill" in warning for warning in result.warnings), result.warnings


def test_a_navigation_assertion_must_not_hold_before_the_change() -> None:
    """Odoo saves a new lead to ``/odoo/crm/12`` from ``/odoo/crm/new``.

    Both collapse to ``/odoo/crm/``, so the old assertion was already true on the form the save was
    sent from: a refused save verified. The pattern keeps the record id's shape instead.
    """
    events = search_recording()
    for event in events:
        if event["step"] and event["step"]["id"] == "click-2":
            event["url"] = FORM
    result = compile_recording(events, options(include_navigate=False))
    commit = next(step for step in result.recipe["steps"] if step.get("commit") is True)
    assert commit["assert_after"]["url_matches"] == "/odoo/crm/\\d+", commit["assert_after"]
