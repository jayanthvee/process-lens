"""The migration runner: filename order, one transaction per file, drift detection."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest

from db.migrate import (
    DEFAULT_MIGRATIONS_DIR,
    MigrationError,
    apply_migrations,
    migration_files,
    plan,
    sha256_of,
)

MIGRATIONS = Path(DEFAULT_MIGRATIONS_DIR)


def _quiet(_message: str) -> None:
    """The runner logs what it does; the tests stay silent."""


def _table_names(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        return {row[0] for row in cur.fetchall()}


def _recorded(dsn: str) -> dict[str, str]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT filename, sha256 FROM schema_migrations ORDER BY filename")
        return {str(name): str(digest) for name, digest in cur.fetchall()}


def _copy_migration(target: Path, name: str) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    copy = target / name
    copy.write_text((MIGRATIONS / name).read_text(encoding="utf-8"), encoding="utf-8")
    return copy


# --- applying the shipped migrations ---------------------------------------


def test_shipped_migrations_apply_and_record_their_hash(
    scratch_database: Callable[[], str],
) -> None:
    dsn = scratch_database()

    applied = apply_migrations(dsn, MIGRATIONS, log=_quiet)

    assert applied == ["0001_init.sql", "0002_ledger_hardening.sql"]
    assert _recorded(dsn) == {
        "0001_init.sql": sha256_of(MIGRATIONS / "0001_init.sql"),
        "0002_ledger_hardening.sql": sha256_of(MIGRATIONS / "0002_ledger_hardening.sql"),
    }
    assert "run_records" in _table_names(dsn)


def test_second_run_applies_nothing(scratch_database: Callable[[], str]) -> None:
    dsn = scratch_database()
    apply_migrations(dsn, MIGRATIONS, log=_quiet)

    assert apply_migrations(dsn, MIGRATIONS, log=_quiet) == []


# --- drift detection -------------------------------------------------------


def test_a_changed_migration_is_refused(
    scratch_database: Callable[[], str], tmp_path: Path
) -> None:
    dsn = scratch_database()
    applied_copy = _copy_migration(tmp_path, "0001_init.sql")
    apply_migrations(dsn, tmp_path, log=_quiet)
    applied_copy.write_text(
        applied_copy.read_text(encoding="utf-8") + "\n-- an edit after the fact\n", encoding="utf-8"
    )

    with pytest.raises(MigrationError) as caught:
        apply_migrations(dsn, tmp_path, log=_quiet)

    message = str(caught.value)
    assert "refusing to run" in message
    assert "0001_init.sql" in message
    assert "immutable" in message


def test_a_recorded_migration_that_disappeared_is_refused(
    scratch_database: Callable[[], str], tmp_path: Path
) -> None:
    dsn = scratch_database()
    _copy_migration(tmp_path, "0001_init.sql")
    apply_migrations(dsn, tmp_path, log=_quiet)
    (tmp_path / "0001_init.sql").unlink()

    with pytest.raises(MigrationError, match="missing"):
        apply_migrations(dsn, tmp_path, log=_quiet)


def test_an_empty_migrations_directory_is_refused(
    scratch_database: Callable[[], str], tmp_path: Path
) -> None:
    dsn = scratch_database()

    with pytest.raises(MigrationError, match=r"no \*\.sql migration files"):
        apply_migrations(dsn, tmp_path, log=_quiet)


# --- transactions and ordering ---------------------------------------------


def test_files_apply_in_filename_order(scratch_database: Callable[[], str], tmp_path: Path) -> None:
    dsn = scratch_database()
    (tmp_path / "0001_first.sql").write_text(
        "CREATE TABLE first (x int);\nINSERT INTO first VALUES (1);\n", encoding="utf-8"
    )
    # Would fail if it ran before 0001: its source table would not exist yet.
    (tmp_path / "0002_second.sql").write_text(
        "CREATE TABLE second (x int);\nINSERT INTO second SELECT x + 1 FROM first;\n",
        encoding="utf-8",
    )

    applied = apply_migrations(dsn, tmp_path, log=_quiet)

    assert applied == ["0001_first.sql", "0002_second.sql"]
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT x FROM second")
        assert cur.fetchone()[0] == 2


def test_a_failing_migration_leaves_nothing_behind(
    scratch_database: Callable[[], str], tmp_path: Path
) -> None:
    dsn = scratch_database()
    (tmp_path / "0001_broken.sql").write_text(
        "CREATE TABLE half_done (x int);\nSELECT 1 / 0;\n", encoding="utf-8"
    )

    with pytest.raises(MigrationError, match="rolled back"):
        apply_migrations(dsn, tmp_path, log=_quiet)

    assert "half_done" not in _table_names(dsn), "the file's statements were rolled back"
    assert _recorded(dsn) == {}, "a failed file is not recorded as applied"


def test_a_file_that_manages_its_own_transaction_is_supported(
    scratch_database: Callable[[], str], tmp_path: Path
) -> None:
    dsn = scratch_database()
    (tmp_path / "0001_own.sql").write_text(
        "BEGIN;\nCREATE TABLE own_transaction (x int);\nCOMMIT;\n", encoding="utf-8"
    )

    assert apply_migrations(dsn, tmp_path, log=_quiet) == ["0001_own.sql"]
    assert "own_transaction" in _table_names(dsn)
    assert _recorded(dsn) == {"0001_own.sql": sha256_of(tmp_path / "0001_own.sql")}


# --- the plan, on its own --------------------------------------------------


def test_plan_treats_every_file_as_pending_on_a_fresh_database(tmp_path: Path) -> None:
    migration = tmp_path / "0001_a.sql"
    migration.write_text("SELECT 1;\n", encoding="utf-8")

    pending, problems = plan([migration], {}, tmp_path)

    assert pending == [migration]
    assert problems == []


def test_plan_flags_a_changed_file(tmp_path: Path) -> None:
    migration = tmp_path / "0001_a.sql"
    migration.write_text("SELECT 1;\n", encoding="utf-8")

    pending, problems = plan([migration], {"0001_a.sql": "0" * 64}, tmp_path)

    assert pending == []
    assert len(problems) == 1
    assert "0001_a.sql" in problems[0]
    assert "immutable" in problems[0]


def test_plan_flags_a_recorded_file_that_is_gone(tmp_path: Path) -> None:
    pending, problems = plan([], {"0001_gone.sql": "0" * 64}, tmp_path)

    assert pending == []
    assert len(problems) == 1
    assert "0001_gone.sql" in problems[0]
    assert "missing" in problems[0]


def test_migration_files_are_ordered_by_name_and_only_sql(tmp_path: Path) -> None:
    for name in ("0003_c.sql", "0001_a.sql", "0002_b.sql", "notes.txt"):
        (tmp_path / name).write_text("-- placeholder\n", encoding="utf-8")

    assert [path.name for path in migration_files(tmp_path)] == [
        "0001_a.sql",
        "0002_b.sql",
        "0003_c.sql",
    ]


def test_migration_files_reports_a_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="not found"):
        migration_files(tmp_path / "absent")
