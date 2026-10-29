"""PL-129: the owner's price change of 2026-10-29, taught with Odoo's Save click.

The owner searched Oak Standing Desk, opened its card, typed 200 over 222.00 and pressed Odoo's
"Save manually" cloud icon. The panel read the shape correctly (an update, saved by that click), but
the compile refused it: "ProcessLens couldn't see anything change after Save manually". On an Odoo
form a save changes neither the address nor the page's words, so nothing after the click can show
that it worked. The same change saved by leaving the page compiled, because the leave re-opens the
item and reads the price back. A Save click on an update is checked the same way: the click is
kept, then the run leaves by the list's address, re-opens the item and reads the value back.
"""

from __future__ import annotations

from typing import Any

from packages.compiler.shape import read_shape
from tests.api.test_pl125_shape import (
    ITEM,
    LIST,
    PRICE,
    SEARCH,
    _compile,
    _event,
    _odoo_fills,
    _steps,
)

SAVE = [
    {"by": "role", "role": "button", "name": "Save manually"},
    {"by": "css", "selector": "button.o_form_button_save"},
]


def _save_holder(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """The top-level step that carries the Save click."""
    return next(
        step
        for step in steps
        if step.get("name") == "Click Save manually"
        or any(inner.get("name") == "Click Save manually" for inner in step.get("then") or [])
    )


def saved_with_the_cloud_icon() -> list[dict[str, Any]]:
    return [
        _event("navigate", LIST),
        _event(
            "fill", LIST, SEARCH, name="Fill Search...", value="oak standing desk", page_set=False
        ),
        _event("press", LIST, SEARCH, name="Press Enter", key="Enter"),
        _event(
            "click",
            LIST,
            [{"by": "role", "role": "generic", "name": "$ 222.00"}],
            name="Click $ 222.00",
        ),
        *_odoo_fills("222.00"),
        _event("fill", ITEM, PRICE, name="Fill Sales Price", value="200", page_set=False),
        _event("fill", ITEM, PRICE, name="Fill Sales Price", value="200.00", page_set=True),
        _event("click", ITEM, SAVE, name="Click Save manually"),
    ]


def test_the_shape_is_an_update_saved_by_the_click() -> None:
    shape = read_shape(saved_with_the_cloud_icon())
    assert shape.kind == "update"
    assert shape.save == "button"
    assert [(change.column, change.value) for change in shape.changes] == [("sales_price", "200")]


def test_a_save_click_on_an_update_compiles_and_reads_the_value_back() -> None:
    recipe = _compile(saved_with_the_cloud_icon(), save="button")
    steps = _steps(recipe)

    holder = _save_holder(steps)
    save = holder["then"][0]
    assert save.get("commit") is not True, (
        "the read-back after re-opening is the check, not the click"
    )
    after_save = steps[steps.index(holder) + 1 :]
    assert after_save[0]["action"] == "navigate" and after_save[0]["url"].endswith("/odoo/products")

    commits = [step for step in steps if step.get("commit")]
    assert len(commits) == 1
    check = commits[0]["assert_after"]["value_equals"]
    assert check["value"] == {"kind": "input", "column": "sales_price"}
    assert recipe["meta"]["idempotency_key"] != recipe["meta"]["record_key"]


def test_the_save_click_comes_after_the_change_and_before_the_leave() -> None:
    steps = _steps(_compile(saved_with_the_cloud_icon(), save="button"))
    names = [step.get("name") or step["action"] for step in steps]
    fill = names.index("Fill Sales Price")
    save = steps.index(_save_holder(steps))
    assert fill < save
    assert steps[save + 1]["action"] == "navigate"


def test_the_save_click_is_pressed_only_when_the_page_offers_it() -> None:
    # Odoo shows its Save icon only on a changed form. A row whose item already has the value
    # changes nothing, the icon is not there, and a plain click would fail the row; the re-open's
    # read-back decides that row instead.
    steps = _steps(_compile(saved_with_the_cloud_icon(), save="button"))
    branch = _save_holder(steps)
    assert branch["action"] == "branch"
    assert branch["when"]["element_present"]["targets"] == branch["then"][0]["targets"]
    assert "else" not in branch
    assert steps[steps.index(branch) + 1]["action"] == "navigate"
