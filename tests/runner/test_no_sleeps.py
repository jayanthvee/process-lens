"""The runner's hard rules: no fixed sleeps, and no model SDK in the batch path (D-017)."""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
RUNNER = PACKAGE_ROOT / "services" / "runner"
LEDGER = PACKAGE_ROOT / "packages" / "ledger"

MODEL_SDKS = (
    "anthropic",
    "openai",
    "google.generativeai",
    "google.generativeai",
    "deepseek",
    "litellm",
)


def _sources(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def test_runner_has_no_fixed_sleep_or_time_sleep() -> None:
    offenders: list[str] = []
    for path in _sources(RUNNER):
        text = path.read_text(encoding="utf-8")
        for needle in ("time.sleep", "sleep("):
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
    assert offenders == [], (
        f"the runner must not sleep; waits are conditions with timeouts: {offenders}"
    )


def test_no_model_sdk_imported_under_runner_or_ledger() -> None:
    offenders: list[str] = []
    for root in (RUNNER, LEDGER):
        for path in _sources(root):
            text = path.read_text(encoding="utf-8")
            for sdk in MODEL_SDKS:
                if f"import {sdk}" in text or f"from {sdk}" in text:
                    offenders.append(f"{path.name}: {sdk}")
    assert offenders == [], f"no model call may live in the batch path (D-017): {offenders}"
