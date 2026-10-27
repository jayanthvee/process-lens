/**
 * The shapes the API returns, exactly as the ticket freezes them.
 *
 * Every screen reads these types, so a page never invents a field: if a value is not here, the API
 * does not send it yet.
 */

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

/** `GET /api/v1/runs/{id}`. `started_at`/`finished_at` are what a finished run's time saved needs. */
export interface RunDetail {
  id: string;
  status: string;
  pause_reason: string | null;
  counts: Record<string, number>;
  open_approval: ApprovalRequest | null;
  /** PL-112: the extension's own reason a code read failed, while the run is paused. */
  pause_detail?: string | null;
  /** PL-067: set while a `site_daily_cap` pause stands, so the page can name the site and limit. */
  site_cap?: { origin: string; limit: number; used: number } | null;
  started_at?: string | null;
  finished_at?: string | null;
  /**
   * The run this one is waiting behind: set only while it is queued and another run on the same
   * app is running or paused (PL-041).
   */
  waiting_behind?: WaitingBehind | null;
}

/** The run ahead of a queued one on the same app, named in plain words. */
export interface WaitingBehind {
  run_id: string;
  task_name: string;
  status: string;
}

/** One row of `GET /api/v1/runs/{id}/records`: a row of the spreadsheet, and where it got to. */
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

/** An approval request a run is waiting on. */
export interface ApprovalRequest {
  id: string;
  run_record_id: string;
  step_id: string;
  kind: string;
  shown: Record<string, unknown>;
  created_at: string;
}

export type ApprovalDecision = "approved" | "rejected";

/** The body of `POST /api/v1/approvals`. */
export interface ApprovalDecisionBody {
  request_id: string;
  decision: ApprovalDecision;
  decided_by: string;
  reason?: string;
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

/** One row of `GET /api/v1/needs-help`, and one entry of `home.needs_help`. */
export interface NeedsHelpRow {
  record_id: string;
  run_id: string;
  task_name: string;
  row: string;
  state: string;
  reason: string | null;
  commit_attempted: boolean;
}

/** One running run on `GET /api/v1/home`. */
export interface RunningRun {
  run_id: string;
  task_name: string;
  done: number;
  total: number;
  already_there: number;
}

/** One task on `GET /api/v1/tasks`, and one entry of `home.tasks`. */
export interface TaskRow {
  workflow_id: string;
  recipe_version_id: string;
  name: string;
  app: string | null;
  last_run: TaskLastRun | null;
  /**
   * Set only on `GET /api/v1/tasks`, and only while the task's last two finished runs both added
   * nothing and stopped for the same reason (PL-041).
   */
  keeps_failing?: KeepsFailing | null;
  /**
   * PL-105: the columns this task's pinned recipe version asks a spreadsheet for. Empty means the
   * task needs no spreadsheet and can be run once; set on `GET /api/v1/tasks` only.
   */
  inputs?: string[];
  /** PL-112: this task signs in with a code emailed to one picked address. */
  sign_in?: boolean;
  /** PL-112: the column that address was typed into while teaching. */
  sign_in_column?: string | null;
  /** PL-112: the address used while teaching (the recipe's own example). */
  sign_in_email?: string | null;
}

/** The reason a task's last two runs both stopped for, in the runner's own words. */
export interface KeepsFailing {
  reason: string;
}

/** A task's most recent run. */
export interface TaskLastRun {
  run_id: string;
  status: string;
  started_at: string | null;
  counts: Record<string, number>;
  /** PL-112: why a paused sign-in run stopped, in the codes the engine already sends. */
  pause_reason?: string | null;
  /** PL-112: the extension's own reason a code read failed (none, ambiguous, not_allowed). */
  pause_detail?: string | null;
}

/** `GET /api/v1/home`. */
export interface HomeSummary {
  window_days: number;
  counts: { done: number; already_there: number; needs_help: number };
  minutes_saved: number;
  needs_help: NeedsHelpRow[];
  running: RunningRun[];
  tasks: TaskRow[];
}

/** `GET /api/v1/executor/session`: whether the Chrome extension is attached right now. */
export interface ExecutorSession {
  attached: boolean;
}

/** One task on `GET /api/v1/apps`: the task's id, which opens it, and its name. */
export interface AppTask {
  id: string;
  name: string;
}

/** One row of `GET /api/v1/apps`: a site a task may use, and the newest run of any of them. */
export interface AppRow {
  name: string;
  host: string;
  origin: string;
  tasks: AppTask[];
  last_run_at: string | null;
  last_run_status: string | null;
}

/**
 * One row of `GET /api/v1/spreadsheets`: an uploaded batch, its task, and the runs that used it.
 *
 * `file_name` is "Pasted rows" when there is no file. `runs` is how many runs used the batch, and
 * `last_run_id` is the newest of them, or `null` when nothing has run on it yet.
 */
export interface SpreadsheetRow {
  id: string;
  file_name: string;
  task_id: string;
  task_name: string;
  row_count: number;
  uploaded_at: string;
  uploaded_by: string | null;
  runs: number;
  last_run_id: string | null;
}

/**
 * `GET /api/v1/workspace/state`: whether this workspace's runs may start at all.
 *
 * `extension_out_of_date` is the API's own verdict, so the page never compares versions itself:
 * the answer is true only when a report exists and it is below `min_extension_version`.
 */
export interface WorkspaceState {
  tenant_id: string;
  runs_frozen: boolean;
  min_extension_version: string | null;
  extension_version: string | null;
  extension_out_of_date: boolean;
}

/** `POST /api/v1/batches`: the batch a spreadsheet became, and what is in it. */
export interface BatchUpload {
  batch_id: string;
  row_count: number;
  columns: string[];
}

/**
 * One entry of a check's `mapping`: one column the task needs.
 *
 * `label` is how a person reads it ("Contact name"), `chosen` is the spreadsheet column the check
 * picked or `null`, and `confidence` is how sure the match is: exact, close or none.
 */
export interface MappingEntry {
  column: string;
  label: string;
  chosen: string | null;
  confidence: "exact" | "close" | "none";
  required: boolean;
  alternatives: string[];
  /** PL-109 item 1: the value the person typed while teaching, never a default for a row. */
  example?: string | null;
}

/** One row of `check.rows_need_a_look`: the row's number and one plain reason. */
export interface NeedALook {
  row: number;
  reason: string;
}

/** `check.first_ready`: the first row that is ready, as the task's own columns see it. */
export interface FirstReady {
  row: number;
  values: Record<string, unknown>;
}

/** `GET /api/v1/batches/{id}/check`: the mapping, the row counts, and the rows to look at. */
export interface BatchCheck {
  batch_id: string;
  row_count: number;
  columns: string[];
  recipe_name: string;
  mapping: MappingEntry[];
  rows_ok: number;
  rows_need_a_look: NeedALook[];
  first_ready: FirstReady | null;
}

/** The body of `PUT /api/v1/batches/{id}/mapping`, and the API's answer. */
export interface MappingBody {
  mapping: Record<string, string | null>;
}

/** `POST /api/v1/runs`: the run that was just created. */
export interface RunCreated {
  run_id: string;
  status: string;
}

/**
 * One step of a task, in plain words (builder C builds the sentences; the type is shared so every
 * page shows a step the same way).
 */
export interface StepWords {
  /** One sentence: "Open Odoo CRM", "Type the email", "Click Save". */
  title: string;
  /** An optional second line, such as the value a step fills in. */
  detail?: string;
}
