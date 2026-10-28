"""PL-123 item 3: the leave is found after the owner's stray clicks.

The recording is rebuilt from ``tests/fixtures/owner/price_update_kanban.json``: the same steps,
targets and order, plus the form-load fills the page did by itself and the stray clicks the owner
made looking for the way back. With teaching's cleaning stubbed to drop the strays, the compile is
search -> open -> set Sales Price -> back, with a saves-by-itself commit and a read-back. Without
cleaning, the compile names the leave it could not recognise instead of guessing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.compiler import CompileOptions, compile_recording

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "owner"
    / "price_update_kanban.json"
)
LIST = "http://localhost:8069/odoo/products"
RECORD = "http://localhost:8069/odoo/products/17"

SEARCH = [
    {"by": "role", "name": "Search...", "role": "searchbox"},
    {"by": "css", "selector": "input.o_searchview_input.o_input.d-print-none"},
]
SUGGESTION = [
    {"by": "role", "name": "Search Product for: Oak Standing Desk", "role": "listitem"},
    {"by": "css", "selector": "li.o_menu_item.dropdown-item.focus"},
]
CARD = [
    {"by": "role", "name": "Oak Standing Desk", "role": "generic"},
    {"by": "css", "selector": "main > div:nth-of-type(1) > div > span"},
]
PRICE = [
    {"by": "role", "name": "Sales Price", "role": "textbox"},
    {"by": "label", "text": "Sales Price"},
    {"by": "css", "selector": "#list_price_0"},
]
BACK = [{"by": "css", "selector": "button.o_back_button.btn.btn-link"}]
STRAY = {
    "all-apps": [
        {"by": "role", "name": "All Apps", "role": "link"},
        {"by": "css", "selector": "a.btn"},
    ],
    "tabs": [
        {"by": "role", "name": "General InformationSalesInventory", "role": "list"},
        {"by": "css", "selector": "ul.nav.nav-tabs.flex-row"},
    ],
    "close": [
        {"by": "role", "name": "Close menu", "role": "button"},
        {"by": "css", "selector": "button.o_sidebar_close.oi.oi-close"},
    ],
}

# The form-load fills the owner saw: "on" twice, Goods/Service/Combo, Track inventory, the old
# price, the cost, the category and the reference. Thirteen events, as the panel showed.
FORM_LOAD = [
    ("on-a", SEARCH, "on"),
    ("on-b", SEARCH, "on"),
    ("goods", [{"by": "role", "role": "radio", "name": "Goods"}], "on"),
    ("service", [{"by": "role", "role": "radio", "name": "Service"}], "on"),
    ("combo", [{"by": "role", "role": "radio", "name": "Combo"}], "on"),
    ("track", [{"by": "role", "role": "checkbox", "name": "Track inventory"}], "on"),
    ("old-price", PRICE, "910.00"),
    ("cost", [{"by": "role", "role": "textbox", "name": "Cost"}], "455.00"),
    ("category", [{"by": "role", "role": "textbox", "name": "Category"}], "All"),
    ("reference", [{"by": "role", "role": "textbox", "name": "Reference"}], "PL-DESK-1001"),
    ("on-c", SEARCH, "on"),
    ("on-d", SEARCH, "on"),
    ("on-e", SEARCH, "on"),
]


def _event(
    seq: int, kind: str, step: dict[str, Any] | None, url: str, **extra: Any
) -> dict[str, Any]:
    event: dict[str, Any] = {"seq": seq, "kind": kind, "url": url, "step": step}
    event.update(extra)
    return event


def _fill(
    step_id: str, targets: list[dict[str, Any]], value: str, url: str, **extra: Any
) -> dict[str, Any]:
    return {
        "id": step_id,
        "action": "fill",
        "targets": targets,
        "value": {"kind": "constant", "value": value},
        **extra,
    }


def _click(step_id: str, targets: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return {"id": step_id, "action": "click", "targets": targets, "name": name}


def owner_recording(*, with_strays: bool = True) -> list[dict[str, Any]]:
    """The owner's demonstration, in the order the recorder saw it."""
    events: list[dict[str, Any]] = [
        _event(1, "navigate", None, LIST),
        _event(2, "fill", _fill("fill-1", SEARCH, "Oak Standing Desk", LIST), LIST),
        _event(
            3,
            "click",
            _click("click-1", SUGGESTION, "Click Search Product for: Oak Standing Desk"),
            LIST,
        ),
        _event(4, "click", _click("click-2", CARD, "Click Oak Standing Desk"), LIST),
    ]
    seq = 5
    for index, (_step_id, targets, value) in enumerate(FORM_LOAD):
        events.append(
            _event(
                seq,
                "fill",
                _fill(f"page-set-{index + 1}", targets, value, RECORD),
                RECORD,
                page_set=True,
                keystrokes=0,
            )
        )
        seq += 1
    events.append(_event(seq, "fill", _fill("fill-2", PRICE, "400", RECORD), RECORD))
    seq += 1
    if with_strays:
        for key, targets in STRAY.items():
            events.append(_event(seq, "click", _click(f"click-{key}", targets, key), RECORD))
            seq += 1
    events.append(_event(seq, "click", _click("click-6", BACK, "Click button"), RECORD))
    return events


def _options(**overrides: Any) -> CompileOptions:
    values: dict[str, Any] = {
        "name": "pricetestoct27",
        "when_to_use": "pricetestoct27",
        "sample_row": {"product": "Oak Standing Desk", "new_price": "400"},
        "record_key_column": "product",
        "input_fields": {
            "product": {"type": "string", "required": True},
            "new_price": {"type": "string", "required": True},
        },
        "commit_step_ids": [],
        "save_by_itself": True,
    }
    values.update(overrides)
    return CompileOptions(**values)  # type: ignore[arg-type]


def _steps(result: Any) -> list[dict[str, Any]]:
    return result.recipe["steps"]


def _commits(result: Any) -> list[dict[str, Any]]:
    return [step for step in _steps(result) if step.get("commit") is True]


def test_the_owner_fixture_compiles_to_a_commit_and_a_read_back_after_cleaning() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["recipe"]["steps"][-1]["id"] == "end-record-unchecked"

    result = compile_recording(
        owner_recording(),
        _options(
            dropped_step_ids=["click-all-apps", "click-tabs", "click-close"],
            intents={
                "click-2": "open the product found by {{row.product}}",
                "click-6": "go back to the product list",
            },
        ),
    )
    steps = _steps(result)
    commits = _commits(result)

    assert len(commits) == 1, steps
    assert "value_equals" in commits[0]["assert_after"], commits[0]
    assert commits[0]["assert_after"]["value_equals"]["value"] == {
        "kind": "input",
        "column": "new_price",
    }
    assert not any(step.get("id") == "end-record-unchecked" for step in steps), steps
    # The strays are gone; the leave the owner made is still there.
    names = " ".join(step.get("name") or "" for step in steps)
    assert "All Apps" not in names and "Close menu" not in names, names
    assert any(step["targets"] == BACK for step in steps if step["action"] == "click"), steps
    leave = next(step for step in steps if step["action"] == "click" and step["targets"] == BACK)
    assert leave["intent"] == "go back to the product list"


def test_without_cleaning_the_compile_names_the_leave_it_could_not_recognise() -> None:
    result = compile_recording(owner_recording(), _options())

    assert _commits(result) == [], _steps(result)
    assert any(step.get("id") == "end-record-unchecked" for step in _steps(result)), _steps(result)
    joined = " ".join(result.warnings)
    assert "leave could not be recognised" in joined, result.warnings
