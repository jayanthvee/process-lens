/** The shapes the API returns, exactly as the ticket freezes them. */

export interface RunCounts {
  total: number;
  verified: number;
  skipped: number;
  parked: number;
  failed: number;
  in_progress: number;
}

/** One row of `GET /api/v1/runs`. */
export interface RunSummary {
  id: string;
  status: string;
  pause_reason: string | null;
  created_at: string | null;
  recipe_name: string | null;
  recipe_version: string | null;
  counts: Record<string, number>;
}

/** An approval request. */
export interface ApprovalRequest {
  id: string;
  run_record_id: string;
  step_id: string;
  kind: string;
  shown: Record<string, unknown>;
  created_at: string;
}

/** `GET /api/v1/runs/{id}`. */
export interface RunDetail {
  id: string;
  status: string;
  pause_reason: string | null;
  counts: Partial<RunCounts>;
  open_approval: ApprovalRequest | null;
}

/** One row of `GET /api/v1/runs/{id}/records`. */
export interface RecordRow {
  id: string;
  record_key: string;
  state: string;
  reason: string | null;
  attempts: number;
  max_attempts: number;
  commit_attempted: boolean;
  destination_ref: string | null;
}

/** One row of `GET /api/v1/runs/{id}/transitions`. */
export interface Transition {
  id: number;
  run_record_id: string;
  from_state: string | null;
  to_state: string;
  reason: string | null;
  actor: string | null;
  at: string;
}

/** `GET /api/v1/operator`. */
export interface OperatorInfo {
  operator_id: string;
}

/** The body of `POST /api/v1/records/{id}/requeue` and `/dismiss`. */
export interface RecordActionBody {
  reason?: string;
  destination_checked_by?: string;
}

/** The answer to `POST /api/v1/records/{id}/requeue` and `/dismiss`. */
export interface RecordActionResponse {
  record_id: string;
  state: string;
}

export type ApprovalDecision = "approved" | "rejected";

/** The body of `POST /api/v1/approvals`. */
export interface ApprovalDecisionBody {
  request_id: string;
  decision: ApprovalDecision;
  decided_by: string;
  reason?: string;
}
