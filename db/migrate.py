"""Apply the SQL migrations in ``db/migrations`` exactly once, in filename order.

Each file runs in its own transaction, so a failing migration leaves no partial
schema behind. Every applied file is recorded in ``schema_migrations`` with its
sha256; a second run applies nothing. If a file that was already applied has
changed, the runner refuses to run and says which file it is: applied migrations
are immutable, so the fix is always a new migration file.

A file that manages its own transaction (``BEGIN; ... COMMIT;``) keeps that
boundary - its statements commit together, and the bookkeeping row is written
immediately afterwards, in its own transaction. A file without transaction
control is wrapped: its statements and the bookkeeping row commit together, or
neither does.

Usage::

    python -m db.migrate [--database-url postgresql://...] [--migrations-dir db/migrations]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename   text PRIMARY KEY,
  sha256     text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
)
"""
_RECORD_MIGRATION = "INSERT INTO schema_migrations (filename, sha256) VALUES (%s, %s)"
_READ_APPLIED = "SELECT filename, sha256 FROM schema_migrations"


class MigrationError(RuntimeError):
    """A migration could not be applied safely. The message is meant for a human."""


def read_dotenv(path: str | os.PathLike[str] = DEFAULT_ENV_FILE) -> dict[str, str]:
    """Load ``KEY=VALUE`` lines from a .env file into the environment.

    Real environment variables win, so a shell export or a CI variable always
    overrides the file. A missing file is not an error.
    """
    values: dict[str, str] = {}
    env_file = Path(path)
    if not env_file.is_file():
        return values
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        values[key] = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, values[key])
    return values


def database_url(env_file: str | os.PathLike[str] = DEFAULT_ENV_FILE) -> str:
    """The connection string: ``DATABASE_URL`` from the environment, else from .env."""
    read_dotenv(env_file)
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise MigrationError(
            "DATABASE_URL is not set. Copy .env.example to .env and run `make up`, "
            "or export DATABASE_URL."
        )
    return url


def migration_files(directory: Path = DEFAULT_MIGRATIONS_DIR) -> list[Path]:
    """Every ``*.sql`` file in the migrations directory, ordered by filename."""
    if not directory.is_dir():
        raise MigrationError(f"migrations directory not found: {directory}")
    return sorted(directory.glob("*.sql"), key=lambda path: path.name)


def sha256_of(path: Path) -> str:
    """The hash recorded in ``schema_migrations`` for a migration file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def applied_migrations(conn: psycopg.Connection) -> dict[str, str]:
    """``filename -> sha256`` for everything already applied."""
    with conn.cursor() as cur:
        cur.execute(_READ_APPLIED)
        return {str(name): str(digest) for name, digest in cur.fetchall()}


def plan(
    files: Iterable[Path],
    applied: Mapping[str, str],
    directory: Path = DEFAULT_MIGRATIONS_DIR,
) -> tuple[list[Path], list[str]]:
    """Split the migration set into ``(pending, problems)``.

    A problem is a file whose recorded hash no longer matches the file on disk,
    or a recorded migration whose file has disappeared. Either one means the
    history has drifted and the run must stop.
    """
    problems: list[str] = []
    pending: list[Path] = []
    on_disk: set[str] = set()

    for path in files:
        on_disk.add(path.name)
        recorded = applied.get(path.name)
        digest = sha256_of(path)
        if recorded is None:
            pending.append(path)
        elif recorded != digest:
            problems.append(
                f"{path.name} changed after it was applied "
                f"(recorded {recorded[:12]}, file {digest[:12]}) - "
                "applied migrations are immutable, add a new migration file instead"
            )

    for name in sorted(set(applied) - on_disk):
        problems.append(f"{name} is recorded as applied but is missing from {directory}")

    return pending, problems


def apply_migrations(
    dsn: str,
    directory: Path = DEFAULT_MIGRATIONS_DIR,
    *,
    log: Callable[[str], None] = print,
) -> list[str]:
    """Apply the pending migrations and return the filenames applied by this run.

    The connection runs in autocommit mode so that a file's own transaction
    control behaves exactly as it does under psql: each file's statements are
    committed together, when psycopg leaves the transaction block.
    """
    files = migration_files(directory)

    applied_now: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_MIGRATIONS_DDL)

        recorded = applied_migrations(conn)
        pending, problems = plan(files, recorded, directory)
        if problems:
            raise MigrationError(
                "refusing to run: the migration history has drifted\n  - " + "\n  - ".join(problems)
            )
        if not pending:
            if not recorded:
                raise MigrationError(f"no *.sql migration files found in {directory}")
            log("nothing to apply: the database is up to date")
            return applied_now

        for path in pending:
            sql = path.read_text(encoding="utf-8")
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(sql)
                    with conn.cursor() as cur:
                        cur.execute(_RECORD_MIGRATION, (path.name, sha256_of(path)))
            except psycopg.Error as exc:
                raise MigrationError(
                    f"{path.name} failed and was rolled back: {exc}".strip()
                ) from exc
            log(f"applied {path.name}")
            applied_now.append(path.name)

    return applied_now


def main(argv: list[str] | None = None) -> int:
    """Command line entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        description="Apply the SQL migrations in db/migrations, in filename order, once each."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Postgres connection string. Defaults to DATABASE_URL from the environment or .env.",
    )
    parser.add_argument(
        "--migrations-dir",
        default=str(DEFAULT_MIGRATIONS_DIR),
        help="Directory holding the *.sql migration files.",
    )
    args = parser.parse_args(argv)

    try:
        dsn = args.database_url or database_url()
        apply_migrations(dsn, Path(args.migrations_dir))
    except MigrationError as exc:
        print(f"migrate: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"migrate: cannot use the database: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
