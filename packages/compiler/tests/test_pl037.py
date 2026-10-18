"""PL-037: the owner's third Odoo recording -- a contact picked from the open list, then typing over
the email Odoo copied from it. The save was refused because both entries vanished."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.coalesce import coalesce_fills
from packages.compiler.model import Event

PICK_FIXTURE = Path(__file__).parent / "fixtures" / "odoo_quick_create_owner_pick.json"
SAMPLE = {
    "email": "probe.three@example.test",
    "phone": "5550000033",
    "expected_revenue": "400",
}


def compile_pick(**overrides: Any) -> Any:
    events = json.loads(PICK_FIXTURE.read_text(encoding="utf-8"))
    options = CompileOptions(
        name="Owner pick lead",
        when_to_use="test",
        sample_row=dict(SAMPLE),
        record_key_column="email",
        commit_step_ids=["click-4"],
        allowed_origins=["http://localhost:8069"],
        **overrides,
    )
    return compile_recording(events, options)


def test_the_recording_compiles_with_a_save_check() -> None:
    result = compile_pick()
    add = next(step for step in result.recipe["steps"] if step["id"] == "click-4")
    # The quick-create card shows the contact, so the check counts the picked contact's name.
    assert add["assert_after"]["text_count_increases"] == "Ridgeline Outfitters"


def test_typing_over_a_page_filled_email_keeps_the_typing() -> None:
    steps = compile_pick().recipe["steps"]
    emails = [
        step
        for step in steps
        if step["action"] == "fill" and step["targets"][0].get("name") == "Email"
    ]
    assert len(emails) == 1
    assert emails[0]["value"] == {"kind": "input", "column": "email"}


def test_a_contact_picked_from_the_open_list_is_kept() -> None:
    steps = compile_pick().recipe["steps"]
    picks = [
        step
        for step in steps
        if step["action"] == "click"
        and any(rung.get("role") == "option" for rung in step["targets"])
    ]
    assert [step["targets"][0]["name"] for step in picks] == ["Ridgeline Outfitters"]


def test_the_box_prompt_is_still_dropped() -> None:
    """PL-032's tab fixture still loses its "Start typing..." click."""
    events = json.loads(
        (Path(__file__).parent / "fixtures" / "odoo_quick_create_owner_tab.json").read_text(
            encoding="utf-8"
        )
    )
    options = CompileOptions(
        name="t",
        when_to_use="t",
        sample_row={
            "contact": "Probe Owner Two",
            "email": "probe.two@example.test",
            "phone": "5550000099",
            "expected_revenue": "500",
        },
        record_key_column="email",
        commit_step_ids=["click-4"],
    )
    steps = compile_recording(events, options).recipe["steps"]
    assert not any("Start typing" in json.dumps(step) for step in steps)


def _fill(seq: int, value: str, *, page_set: bool, keystrokes: int) -> Event:
    return Event(
        seq=seq,
        kind="fill",
        at=f"2026-10-18T00:00:0{seq}.000Z",
        url="http://localhost:8069/odoo/crm",
        step={
            "id": f"fill-{seq}",
            "action": "fill",
            "targets": [{"by": "role", "role": "textbox", "name": "Email"}],
            "value": {"kind": "constant", "value": value},
        },
        page_set=page_set,
        keystrokes=keystrokes,
    )


def test_coalescing_takes_the_later_entrys_page_set_flag() -> None:
    folded, _ = coalesce_fills(
        [
            _fill(1, "copied@example.test", page_set=True, keystrokes=0),
            _fill(2, "typed@example.test", page_set=False, keystrokes=18),
        ]
    )
    assert len(folded) == 1
    assert folded[0].page_set is False
    assert folded[0].keystrokes == 18
