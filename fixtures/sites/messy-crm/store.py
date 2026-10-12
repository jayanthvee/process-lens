"""In-memory store and synthetic address book for the messy-crm fixture.

Pure logic: no HTTP, no I/O, no persistence. The store keeps every saved
compose record -- its recipients and its notes -- so a test can ask the
fixture what actually landed, and the directory is the little address book
the recipient combobox filters.

Every address is ``@example.test``: ``example.test`` is reserved (RFC 2606),
and the save refuses anything else, so a stray real address cannot end up in
a test run. All access goes through a ``threading.Lock`` because the fixture
server is a ``ThreadingHTTPServer``.
"""

from __future__ import annotations

import threading

__all__ = [
    "DIRECTORY",
    "STATIC_CHIP",
    "SYNTHETIC_DOMAIN",
    "MessyStore",
    "is_synthetic",
    "parse_recipients",
    "search_directory",
]

#: The only domain this fixture stores or serves.
SYNTHETIC_DOMAIN = "example.test"

#: The address book the combobox offers. The names matter: a real
#: autocomplete matches on the display name as well as on the address, so a
#: recorder has to survive both shapes.
DIRECTORY: list[dict[str, str]] = [
    {"name": "Alice", "address": "alice@example.test"},
    {"name": "Bob", "address": "bob@example.test"},
    {"name": "Carol", "address": "carol@example.test"},
    {"name": "Dana", "address": "dana@example.test"},
    {"name": "Erin", "address": "erin@example.test"},
    {"name": "Frank", "address": "frank@example.test"},
]

#: The pre-rendered chip's address: the one value the served markup always
#: shows, so a save is possible before any script runs.
STATIC_CHIP = "alice@example.test"


def is_synthetic(address: object) -> bool:
    """True when ``address`` is a plain ``local@example.test`` address."""
    text = str(address or "").strip().lower()
    local, _, domain = text.rpartition("@")
    return bool(local) and domain == SYNTHETIC_DOMAIN


def parse_recipients(raw: object) -> list[str]:
    """Split a posted ``recipients`` field into addresses, order preserved.

    Duplicates collapse: the same chip can be added twice by hand, and the
    store should still see one recipient.
    """
    addresses: dict[str, None] = {}
    for piece in str(raw or "").split(","):
        text = piece.strip().lower()
        if text:
            addresses.setdefault(text, None)
    return list(addresses)


def search_directory(query: object, limit: int = 5) -> list[dict[str, str]]:
    """Directory entries matching ``query`` on their name or their address.

    An empty query matches nothing, exactly like a search box: the combobox
    only offers entries once something has been typed. ``limit`` keeps the
    listbox short, the way a real one does.
    """
    needle = str(query or "").strip().lower()
    if not needle:
        return []
    matches = [
        dict(entry)
        for entry in DIRECTORY
        if needle in entry["name"].lower() or needle in entry["address"].lower()
    ]
    return matches[:limit]


class MessyStore:
    """Saved compose records, oldest first."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[dict] = []

    def _snapshot(self) -> list[dict]:
        """A copy of every record, taken under the lock."""
        with self._lock:
            return [dict(entry) for entry in self._records]

    def record(self, recipients: list[str], notes: str = "") -> dict:
        """Store one save and return it. The handler validates first."""
        entry = {
            "recipients": [str(address) for address in recipients],
            "notes": str(notes or ""),
        }
        with self._lock:
            self._records.append(entry)
        return dict(entry)

    def last(self) -> dict | None:
        """The record the saved page reads back, or None before any save."""
        records = self._snapshot()
        return records[-1] if records else None

    def recipient_counts(self) -> dict[str, int]:
        """How many saved records hold each address."""
        counts: dict[str, int] = {}
        for entry in self._snapshot():
            for address in entry["recipients"]:
                counts[address] = counts.get(address, 0) + 1
        return counts

    def notes(self) -> list[str]:
        """Every stored note, in save order."""
        return [entry["notes"] for entry in self._snapshot()]

    def counts(self) -> dict:
        """The shape ``/__test__/state`` reports for the store."""
        records = self._snapshot()
        counts: dict[str, int] = {}
        for entry in records:
            for address in entry["recipients"]:
                counts[address] = counts.get(address, 0) + 1
        return {
            "saves": len(records),
            "recipients": counts,
            "notes": [entry["notes"] for entry in records],
        }

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
