"""PL-044: the save check counts the row's own entry, not a stray one before it.

The owner's two-saves recording starts with an abandoned entry (a New click, a
picked contact, a visit to the contact's own page) before the real entry the
Add at seq 24 belongs to. The inferred save check must come from the entry the
save belongs to -- the steps after the last page change before it -- never from
an earlier one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.compiler import CompileOptions, compile_recording

TWO_SAVES = Path(__file__).parent / "fixtures" / "odoo_two_saves_owner.json"
PICK = Path(__file__).parent / "fixtures" / "odoo_quick_create_owner_pick.json"

# The second entry's own typed values, so every typed fill of the real entry
# binds to its column; the record key is the email.
SAMPLE = {
    "contact": "Probe Five Contact",
    "email": "probe.five.example.test",
    "phone": "5550000055",
    "expected_revenue": "3820",
}


def compile_two_saves(**overrides: Any) -> Any:
    """The two-saves fixture trimmed at seq 24, the way PL-042's firstEntryEvents does."""
    all_events = json.loads(TWO_SAVES.read_text(encoding="utf-8"))
    events = [event for event in all_events if event["seq"] <= 24]
    options = CompileOptions(
        name="Two saves lead",
        when_to_use="test",
        sample_row=dict(SAMPLE),
        record_key_column="email",
        commit_step_ids=["click-8"],
        allowed_origins=["http://localhost:8069"],
        **overrides,
    )
    return compile_recording(events, options)


def commit_step(result: Any) -> dict[str, Any]:
    """The one commit step of these one-save recipes."""
    return next(step for step in result.recipe["steps"] if step.get("commit") is True)


def test_the_save_check_comes_from_the_second_entry() -> None:
    add = commit_step(compile_two_saves())
    check = add["assert_after"]["text_count_increases"]
    assert check != "Probe Four Contact"
    # The abandoned entry's pick ("Probe Four Contact", seq 4) sits before the page change:
    # the Internal link click (seq 9) observed the contact's own page (url_matches
    # /odoo/crm/res.partner/\d+, not the kanban's /odoo/crm). The entry the save belongs to
    # starts at seq 11. Its only option pick is the "Start typing..." hint, which is not an
    # entry, so the check is the second entry's first templated fill: the contact.
    assert check == "{{row.contact}}"


def test_no_page_change_compiles_exactly_as_today() -> None:
    """PL-037's pick recording never leaves the kanban, so nothing is trimmed away."""
    events = json.loads(PICK.read_text(encoding="utf-8"))
    options = CompileOptions(
        name="Owner pick lead",
        when_to_use="test",
        sample_row={
            "email": "probe.three@example.test",
            "phone": "5550000033",
            "expected_revenue": "400",
        },
        record_key_column="email",
        commit_step_ids=["click-4"],
        allowed_origins=["http://localhost:8069"],
    )
    result = compile_recording(events, options)
    add = next(step for step in result.recipe["steps"] if step["id"] == "click-4")
    assert add["assert_after"]["text_count_increases"] == "Ridgeline Outfitters"


def test_a_stray_entry_on_another_page_is_not_counted() -> None:
    """fill A, a click whose observation is on another path, back, fill B, save: B counts."""
    events = [
        {
            "at": "2026-10-18T00:00:01.000Z",
            "kind": "fill",
            "url": "http://app.test/list",
            "seq": 1,
            "step": {
                "id": "fill-a",
                "action": "fill",
                "targets": [{"by": "role", "role": "textbox", "name": "Note"}],
                "value": {"kind": "constant", "value": "Alpha Note"},
            },
        },
        {
            "at": "2026-10-18T00:00:02.000Z",
            "kind": "click",
            "url": "http://app.test/list",
            "seq": 2,
            "step": {
                "id": "open-1",
                "action": "click",
                "targets": [{"by": "role", "role": "button", "name": "Open"}],
            },
        },
        {
            "at": "2026-10-18T00:00:03.000Z",
            "kind": "observation",
            "url": "http://app.test/detail/7",
            "step": None,
            "observation": {"url_matches": "/detail/\\d+"},
            "seq": 3,
        },
        {
            "at": "2026-10-18T00:00:04.000Z",
            "kind": "navigate",
            "url": "http://app.test/list",
            "seq": 4,
        },
        {
            "at": "2026-10-18T00:00:05.000Z",
            "kind": "fill",
            "url": "http://app.test/list",
            "seq": 5,
            "step": {
                "id": "fill-b",
                "action": "fill",
                "targets": [{"by": "role", "role": "textbox", "name": "Email"}],
                "value": {"kind": "constant", "value": "beta@example.test"},
            },
        },
        {
            "at": "2026-10-18T00:00:06.000Z",
            "kind": "click",
            "url": "http://app.test/list",
            "seq": 6,
            "step": {
                "id": "save-1",
                "action": "click",
                "targets": [{"by": "role", "role": "button", "name": "Save"}],
            },
        },
    ]
    options = CompileOptions(
        name="Stray entry",
        when_to_use="test",
        sample_row={"note": "Alpha Note", "email": "beta@example.test"},
        record_key_column="email",
        commit_step_ids=["save-1"],
        allowed_origins=["http://app.test"],
    )
    result = compile_recording(events, options)
    save = commit_step(result)
    assert save["assert_after"]["text_count_increases"] == "{{row.email}}"
