"""In-memory lead store for the synthetic lead-crm fixture.

Pure logic: no HTTP, no I/O, no persistence. Every key is the stripped,
lowercased email, so ``Ana@Example.test`` and ``ana@example.test`` are the
same lead. All access goes through a ``threading.Lock`` because the fixture
server is a ``ThreadingHTTPServer``.
"""

from __future__ import annotations

import threading

__all__ = ["LeadStore"]


class LeadStore:
    """Leads keyed by lowercased email."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.leads: dict[str, dict] = {}

    @staticmethod
    def _key(email: object) -> str:
        return str(email or "").strip().lower()

    def create(self, email: str, interest: str = "", company: str = "") -> bool:
        """Store one lead. False when the email already exists (or is empty)."""
        key = self._key(email)
        if not key:
            return False
        with self._lock:
            if key in self.leads:
                return False
            self.leads[key] = {
                "email": key,
                "interest": str(interest or ""),
                "company": str(company or ""),
            }
            return True

    def search(self, email: str) -> list[dict]:
        """Leads matching one email: zero or one dict, never more."""
        key = self._key(email)
        if not key:
            return []
        with self._lock:
            lead = self.leads.get(key)
            return [dict(lead)] if lead is not None else []

    def count_for(self, email: str) -> int:
        """How many leads exist for one email. Used to catch a duplicate save."""
        key = self._key(email)
        if not key:
            return 0
        with self._lock:
            return 1 if key in self.leads else 0

    def counts(self) -> dict[str, int]:
        """How many leads exist per stored email, for ``/__test__/state``."""
        with self._lock:
            return {key: 1 for key in self.leads}

    def reset(self) -> None:
        with self._lock:
            self.leads.clear()
