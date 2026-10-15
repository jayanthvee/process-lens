"""In-memory store and synthetic directory for the widget-corpus fixture.

Pure logic: no HTTP, no I/O, no persistence. The store keeps

* every message ``POST /send`` accepted -- its recipients, its body and the
  page that sent it -- so a test can ask the fixture what actually landed;
* every ``POST /save`` a widget's Save button made, as
  ``{source: {field: value}}``, so each corpus page has exactly one value the
  fixture can prove;
* the little synthetic address book the autocompletes filter.

Every address is ``@example.test``: ``example.test`` is reserved (RFC 2606),
and nothing else is ever minted here, so a stray real address cannot end up in
a test run. All access goes through a ``threading.Lock`` because the fixture
server is a ``ThreadingHTTPServer``.
"""

from __future__ import annotations

import threading

__all__ = [
    "DIRECTORY",
    "SYNTHETIC_DOMAIN",
    "WidgetStore",
    "is_synthetic",
    "parse_recipients",
    "search_directory",
]

#: The only domain this fixture stores or serves.
SYNTHETIC_DOMAIN = "example.test"

#: The address book the autocompletes offer. The names matter as much as the
#: addresses: a real autocomplete matches on the display name as well as on the
#: address, so a recorder has to survive both shapes. ``Mary An`` is the one
#: entry whose address starts with the typed prefix ``ma``, which is the prefix
#: the ticket types, so a two-character query matches exactly one suggestion.
DIRECTORY: list[dict[str, str]] = [
    {"name": "Alice", "address": "alice@example.test"},
    {"name": "Bob", "address": "bob@example.test"},
    {"name": "Carol", "address": "carol@example.test"},
    {"name": "Dana", "address": "dana@example.test"},
    {"name": "Erin", "address": "erin@example.test"},
    {"name": "Mary An", "address": "mary.an@example.test"},
]

#: The page whose chips the golden exercises.
GOLDEN_SOURCE = "gmail-compose"


def is_synthetic(address: object) -> bool:
    """True when ``address`` is a plain ``local@example.test`` address."""
    text = str(address or "").strip().lower()
    local, _, domain = text.rpartition("@")
    return bool(local) and domain == SYNTHETIC_DOMAIN


def parse_recipients(raw: object) -> list[str]:
    """Split a posted ``to`` field into addresses, order preserved.

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


class WidgetStore:
    """Messages, saved widget values and the counters that describe them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: list[dict] = []
        self._values: dict[str, dict[str, str]] = {}
        self._sends = 0
        self._saves = 0

    # -- messages ---------------------------------------------------------
    def send(self, to: list[str], body: str, source: str = GOLDEN_SOURCE) -> dict:
        """Store one sent message and return it. The handler validates first."""
        message = {
            "to": [str(address) for address in to],
            "body": str(body or ""),
            "source": str(source or GOLDEN_SOURCE),
        }
        with self._lock:
            self._messages.append(message)
            self._sends += 1
        return dict(message)

    def message_list(self) -> list[dict]:
        """Every stored message, oldest first."""
        with self._lock:
            return [dict(entry) for entry in self._messages]

    def last_message(self) -> dict | None:
        """The newest message, or None before anything was sent."""
        messages = self.message_list()
        return messages[-1] if messages else None

    def message_bodies(self) -> list[str]:
        """Every body, in send order: the value each corpus case proves."""
        return [entry["body"] for entry in self.message_list()]

    def recipients(self) -> dict[str, int]:
        """How many stored messages hold each address."""
        counts: dict[str, int] = {}
        for entry in self.message_list():
            for address in entry["to"]:
                counts[address] = counts.get(address, 0) + 1
        return counts

    # -- saved widget values ----------------------------------------------
    def save(self, source: str, fields: dict) -> dict[str, str]:
        """Merge one Save button's fields into that page's stored values."""
        entry = {str(key): str(value) for key, value in dict(fields or {}).items()}
        with self._lock:
            self._values.setdefault(str(source), {}).update(entry)
            self._saves += 1
            return dict(self._values[str(source)])

    def values(self, source: str | None = None) -> dict:
        """One page's saved values, or every page's when ``source`` is None."""
        with self._lock:
            snapshot = {page: dict(entry) for page, entry in self._values.items()}
        if source is None:
            return snapshot
        return snapshot.get(str(source), {})

    # -- the shape the test API serves -------------------------------------
    def counts(self) -> dict:
        """The store's own truth, as ``/__test__/state`` reports it."""
        with self._lock:
            messages = [dict(entry) for entry in self._messages]
            values = {page: dict(entry) for page, entry in self._values.items()}
            sends = self._sends
            saves = self._saves
        return {"messages": messages, "sends": sends, "saves": saves, "values": values}

    def reset(self) -> None:
        with self._lock:
            self._messages.clear()
            self._values.clear()
            self._sends = 0
            self._saves = 0
