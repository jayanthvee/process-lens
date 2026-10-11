"""The approval policy: when a commit must be shown to a person before it is sent.

The policy is evaluated immediately before ``begin_commit`` (R3), so the gate covers exactly the
commit it is about to send. An ``approval_gate`` step is an explicit, unconditional gate the
recipe author
placed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def evaluate_policy(policy: Mapping[str, Any], *, commits_attempted: int, mode: str) -> bool:
    """True when this commit must be approved before it is sent.

    ``preview`` mode always gates. Otherwise ``commit`` decides: ``first_record`` gates the first
    commit of the run (including a batch started without a preview), ``every_record`` gates all,
    ``first_n`` gates the first ``n``, and ``none`` never gates.
    """
    if mode == "preview":
        return True

    rule = policy.get("commit", "first_record")
    if rule == "every_record":
        return True
    if rule == "first_record":
        return commits_attempted == 0
    if rule == "first_n":
        n = policy.get("n")
        return isinstance(n, int) and commits_attempted < n
    return False  # 'none', or an unknown rule the schema should have rejected
