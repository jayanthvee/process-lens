"""Reset or seed the two local test apps.

    python infra/test-apps/reset.py odoo      # drop Odoo's database, create and seed it again
    python infra/test-apps/reset.py twenty    # clear Twenty's records, seed them again
    python infra/test-apps/reset.py all       # both, one after the other
    python infra/test-apps/reset.py all --seed-only   # ensure the data, drop nothing

`all` is what T1 runs between browser test runs. Each app is reset in well under
three minutes; the Twenty path takes seconds, the Odoo path is bounded by
installing its five modules into a fresh database.

The apps must already be up:

    docker compose -f infra/test-apps/docker-compose.yml up -d
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import testapps_env  # noqa: E402  (needs the directory above on sys.path)

# Target name -> the seeder modules it runs, in order.
TARGETS: dict[str, tuple[str, ...]] = {
    "odoo": ("seed_odoo",),
    "twenty": ("seed_twenty",),
    "all": ("seed_odoo", "seed_twenty"),
}


def _load(module_name: str):
    """Import a seeder lazily, so one target never depends on the other's module."""
    try:
        return importlib.import_module(module_name)
    except ImportError as error:
        raise SystemExit(
            f"cannot import {module_name}: {error}. Run this from the repository root, "
            "for example: python infra/test-apps/reset.py all"
        ) from error


def run_target(
    target: str, seed_only: bool = False, log: Callable[[str], None] = print
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run every seeder for one target.

    Returns the summaries that succeeded and the names of the ones that raised.
    `all` is not fail-fast on purpose: the two apps are independent, so a broken
    Odoo run must not hide the fact that Twenty was never reseeded. Every seeder is
    attempted, both outcomes are reported, and the caller exits non-zero if any of
    them failed.
    """
    env = testapps_env.load_env()
    log(
        f"--- {target} ({'seed only' if seed_only else 'reset'}) --- using "
        f"{testapps_env.env_file_in_use().name}"
    )
    summaries: list[dict[str, Any]] = []
    failures: list[str] = []
    for module_name in TARGETS[target]:
        module = _load(module_name)
        action = module.seed if seed_only else module.reset
        started = time.monotonic()
        try:
            summaries.append(action(env, log=log))
        except Exception as error:  # noqa: BLE001 - every seeder failure is reported, never hidden
            failures.append(module_name)
            log(f"{module_name} FAILED after {time.monotonic() - started:.1f}s: {error!r}")
            continue
        log(f"{module_name}: {time.monotonic() - started:.1f}s")
    return summaries, failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reset.py",
        description="Drop the test apps' data and seed it again.",
    )
    parser.add_argument("target", choices=sorted(TARGETS), help="which app to reset")
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="only ensure the data is there; drop nothing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.monotonic()
    summaries, failures = run_target(args.target, seed_only=args.seed_only)
    print(json.dumps(summaries, indent=2, sort_keys=True))
    print(f"total: {time.monotonic() - started:.1f}s")
    if failures:
        print(f"FAILED: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
