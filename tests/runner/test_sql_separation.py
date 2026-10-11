"""The separation rule: ledger-table SQL lives only in ``packages/ledger`` (S3).

The scan covers ``services/`` and ``packages/`` (excluding ``packages/ledger``) only, because the
database tests and ``seed.py`` legitimately contain ledger SQL.
"""

from __future__ import annotations

import re
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (PACKAGE_ROOT / "services", PACKAGE_ROOT / "packages")
LEDGER_PACKAGE = PACKAGE_ROOT / "packages" / "ledger"

LEDGER_TABLES = ("runs", "run_records", "committed_keys", "jobs")

_SQL_TABLE = re.compile(
    r"\b(?:from|into|update|join|table|truncate)\s+(runs|run_records|committed_keys|jobs)\b",
    re.IGNORECASE,
)


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        for path in root.rglob("*.py"):
            if LEDGER_PACKAGE in path.parents:
                continue
            files.append(path)
    return sorted(files)


def test_ledger_table_sql_lives_only_in_packages_ledger() -> None:
    offenders: list[str] = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8")
        for match in _SQL_TABLE.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(PACKAGE_ROOT)}:{line}: {match.group(0)}")
    assert offenders == [], (
        "ledger-table SQL must live only in packages/ledger; found: " + "; ".join(offenders)
    )


def test_the_scan_actually_looks_at_files() -> None:
    scanned = _scanned_files()
    assert scanned, "the separation scan found no files to check"
    assert not any(LEDGER_PACKAGE in path.parents for path in scanned)
    assert any("runner" in str(path) for path in scanned)
