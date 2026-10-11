"""``packages.ledger``: the only place ledger SQL lives.

Every query that names ``runs``, ``run_records``, ``committed_keys`` or ``jobs`` lives here, one
typed function per pattern in ``internal/docs/sql-patterns.md``. Functions take a connection and
never open a transaction of their own: the caller owns the boundary, because the write-ahead pattern
depends on committing at the right moment.
"""

from __future__ import annotations

from .approvals import (
    decisions_for_step,
    get_request,
    open_approval_for_run,
    open_approval_request,
    record_approval,
)
from .commits import (
    begin_commit,
    mark_submitted_unverified,
    skip_already_committed,
    verify_commit,
)
from .errors import IllegalTransition, LedgerError, StaleLease, StateConflict
from .jobs import claim_job
from .lease import require_lease
from .models import (
    ApprovalDecision,
    ApprovalRequestRow,
    JobClaim,
    RecordClaim,
    RecoveryCounts,
    ResultsRow,
    RunClaim,
    RunSummaryRow,
    TransitionRow,
    VerifyResult,
)
from .reads import (
    list_run_records,
    list_transitions,
    results_rows,
    run_of_record,
    run_summary,
)
from .records import (
    change_state,
    claim_record,
    get_record,
    release_working_records,
    requeue_record,
    skip_pending_records,
)
from .recovery import (
    all_records_in_exit_state,
    close_out_exhausted,
    commits_attempted,
    count_not_exit,
    reconciling_records,
    recover_run,
)
from .runs import (
    claim_run,
    find_stale_run,
    get_run,
    heartbeat,
    request_cancel,
    resume_run,
    set_run_status,
    take_over_run,
)
from .steps import record_step_event
from .transitions import COMMIT_WINDOW_STATES, EXIT_STATES, LEGAL_TRANSITIONS, WORKING_STATES

__all__ = [
    "COMMIT_WINDOW_STATES",
    "EXIT_STATES",
    "LEGAL_TRANSITIONS",
    "WORKING_STATES",
    "ApprovalDecision",
    "ApprovalRequestRow",
    "IllegalTransition",
    "JobClaim",
    "LedgerError",
    "RecoveryCounts",
    "RecordClaim",
    "ResultsRow",
    "RunClaim",
    "RunSummaryRow",
    "StaleLease",
    "StateConflict",
    "TransitionRow",
    "VerifyResult",
    "all_records_in_exit_state",
    "begin_commit",
    "change_state",
    "claim_job",
    "claim_record",
    "claim_run",
    "close_out_exhausted",
    "commits_attempted",
    "count_not_exit",
    "decisions_for_step",
    "find_stale_run",
    "get_record",
    "get_request",
    "get_run",
    "heartbeat",
    "list_run_records",
    "list_transitions",
    "mark_submitted_unverified",
    "open_approval_for_run",
    "open_approval_request",
    "record_approval",
    "record_step_event",
    "recover_run",
    "reconciling_records",
    "release_working_records",
    "request_cancel",
    "requeue_record",
    "require_lease",
    "results_rows",
    "resume_run",
    "run_of_record",
    "run_summary",
    "set_run_status",
    "skip_already_committed",
    "skip_pending_records",
    "take_over_run",
    "verify_commit",
]
