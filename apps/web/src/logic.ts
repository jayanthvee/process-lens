/**
 * The pure decisions this page makes: which buttons exist, what a pause reason means, when the
 * destination check gates an action, which ids a request body carries, and how the poll runs.
 *
 * Everything here is a plain exported function with no DOM in sight, so the tests next to it are
 * honest unit tests instead of DOM scraping.
 */

import type { ApprovalDecision, ApprovalDecisionBody, RecordActionBody } from "./types";

export const POLL_INTERVAL_MS = 2000;

/** The exact words the confirmation carries. The ticket fixes them. */
export const DESTINATION_CHECK_LABEL = "I checked the site and this record is not there";

/** Said once, near the top of the page, when `GET /api/v1/operator` refuses. */
export const OPERATOR_UNAVAILABLE_MESSAGE =
  "The API did not give this page an operator identity, so every action that has to record who did it is disabled. Set PROCESSLENS_OPERATOR_ID and restart the API.";

export type RunStatus = "queued" | "running" | "paused" | "completed" | "cancelled" | "failed";

export const RUN_STATUSES: readonly RunStatus[] = [
  "queued",
  "running",
  "paused",
  "completed",
  "cancelled",
  "failed",
];

/** "Open" is queued, running or paused. */
export const OPEN_STATUSES: readonly string[] = ["queued", "running", "paused"];

export function isOpenStatus(status: string): boolean {
  return OPEN_STATUSES.includes(status);
}

export function isFinishedStatus(status: string): boolean {
  return isOpenStatus(status) === false;
}

/** Poll while — and only while — the run is open. */
export function shouldPoll(status: string | null | undefined): boolean {
  if (status === null || status === undefined) return false;
  return isOpenStatus(status);
}

export interface RunActionAvailability {
  pause: boolean;
  resume: boolean;
  cancel: boolean;
}

/** Pause and Cancel for an open run; Resume only when the run is paused. */
export function runActions(status: string): RunActionAvailability {
  const open = isOpenStatus(status);
  return { pause: open, resume: status === "paused", cancel: open };
}

export type RunAction = "pause" | "resume" | "cancel";

export function runActionLabel(action: RunAction): string {
  if (action === "pause") return "Pause";
  if (action === "resume") return "Resume";
  return "Cancel";
}

/** "/" + words, with no host: the page and the API share one origin. */
function path(...segments: string[]): string {
  return `/api/v1/${segments.join("/")}`;
}

export function runsPath(): string {
  return path("runs");
}

export function runPath(runId: string): string {
  return path("runs", encodeURIComponent(runId));
}

export function runActionPath(runId: string, action: RunAction): string {
  return path("runs", encodeURIComponent(runId), action);
}

export function recordsPath(runId: string): string {
  return path("runs", encodeURIComponent(runId), "records");
}

export function transitionsPath(runId: string, after = 0): string {
  const cursor = Number.isFinite(after) ? Math.max(0, Math.trunc(after)) : 0;
  return `${path("runs", encodeURIComponent(runId), "transitions")}?after=${cursor}`;
}

export function approvalsPath(runId: string): string {
  return path("runs", encodeURIComponent(runId), "approvals");
}

export function resultsCsvPath(runId: string): string {
  return path("runs", encodeURIComponent(runId), "results.csv");
}

export function operatorPath(): string {
  return path("operator");
}

export function approvalDecisionPath(): string {
  return path("approvals");
}

export type RecordAction = "requeue" | "dismiss";

export function recordActionPath(recordId: string, action: RecordAction): string {
  return path("records", encodeURIComponent(recordId), action);
}

export function runHash(runId: string): string {
  return `#/runs/${encodeURIComponent(runId)}`;
}

export interface RunsRoute {
  name: "runs";
}

export interface RunRoute {
  name: "run";
  id: string;
}

export type Route = RunsRoute | RunRoute;

/** The hash router: one page, no server-side rewrite, so `StaticFiles` needs no fallback. */
export function routeFromHash(hash: string): Route {
  const withoutHash = hash.startsWith("#") ? hash.slice(1) : hash;
  const withSlash = withoutHash.startsWith("/") ? withoutHash : `/${withoutHash}`;
  const match = /^\/runs\/([^/?#]+)/.exec(withSlash);
  const captured = match === null ? undefined : match[1];
  if (captured !== undefined && captured !== "") {
    return { name: "run", id: decodeURIComponent(captured) };
  }
  return { name: "runs" };
}

/** "login_required" and friends, capitalised and readable. */
export function humanizeToken(token: string): string {
  const words = token.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  if (words === "") return "";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const RUN_STATUS_TEXT: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  paused: "Paused",
  completed: "Completed",
  cancelled: "Cancelled",
  failed: "Failed",
};

export function humanizeStatus(status: string): string {
  const known = RUN_STATUS_TEXT[status];
  return known === undefined ? humanizeToken(status) : known;
}

const PAUSE_REASON_TEXT: Record<string, string> = {
  login_required: "Log in to the site in the bound tab, then resume.",
  challenge: "Solve the security challenge the site is showing in the bound tab, then resume.",
  approval_required: "An approval request is waiting for your decision, then resume.",
  awaiting_approval: "An approval request is waiting for your decision, then resume.",
  operator_paused: "You paused this run. Resume when you are ready.",
  manual_review: "A record in the table below needs your decision, then resume.",
  rate_limited: "The site is rate limiting us. Wait a moment, then resume.",
  destination_unreachable: "The site could not be reached. Check it, then resume.",
};

/** The pause reason in words a person understands, or null when there is no reason. */
export function humanizePauseReason(reason: string | null | undefined): string | null {
  if (reason === null || reason === undefined) return null;
  const key = reason.trim().toLowerCase();
  if (key === "") return null;
  const known = PAUSE_REASON_TEXT[key];
  if (known !== undefined) return known;
  return `Paused: ${humanizeToken(key)}. Sort that out, then resume.`;
}

export const COUNT_ORDER: readonly string[] = [
  "total",
  "verified",
  "skipped",
  "parked",
  "failed",
  "in_progress",
];

export interface CountEntry {
  key: string;
  label: string;
  value: number;
}

/** Every count the API sent, in a stable order. */
export function countEntries(counts: Record<string, number | undefined>): CountEntry[] {
  const keys = [...COUNT_ORDER, ...Object.keys(counts).filter((key) => COUNT_ORDER.includes(key) === false)];
  const entries: CountEntry[] = [];
  for (const key of keys) {
    const value = counts[key];
    if (value === undefined) continue;
    entries.push({ key, label: humanizeToken(key), value });
  }
  return entries;
}

export interface RecordActions {
  requeue: boolean;
  dismiss: boolean;
}

/** Re-queue and Dismiss appear for parked and failed records only. */
export function recordActions(state: string): RecordActions {
  const actionable = state === "parked" || state === "failed";
  return { requeue: actionable, dismiss: actionable };
}

export interface ActionGateInput {
  state: string;
  commitAttempted: boolean;
  confirmed: boolean;
  operatorId: string | null;
}

export interface ActionGate {
  shown: boolean;
  /** A commit-attempted record needs the destination check ticked first. */
  needsConfirmation: boolean;
  /** Only the checked-destination path has to send the operator id. */
  needsOperator: boolean;
  disabled: boolean;
}

/**
 * The one place the page can get past the ledger's duplicate protection, so it is never one click.
 * A commit-attempted record keeps both buttons disabled until the confirmation is ticked, and
 * clearing the tick disables them again.
 */
export function actionGate(input: ActionGateInput): ActionGate {
  const actions = recordActions(input.state);
  if (actions.requeue === false) {
    return { shown: false, needsConfirmation: false, needsOperator: false, disabled: true };
  }
  const needsConfirmation = input.commitAttempted === true;
  const needsOperator = needsConfirmation;
  const confirmationMissing = needsConfirmation && input.confirmed !== true;
  const operatorMissing = needsOperator && (input.operatorId === null || input.operatorId === "");
  return {
    shown: true,
    needsConfirmation,
    needsOperator,
    disabled: confirmationMissing || operatorMissing,
  };
}

export interface ActionBodyInput {
  commitAttempted: boolean;
  confirmed: boolean;
  operatorId: string | null;
  reason?: string | null;
}

/**
 * The exact body the record endpoints get. `destination_checked_by` is sent only after the check
 * has been confirmed, and the id is never invented.
 */
export function actionBody(input: ActionBodyInput): RecordActionBody {
  const body: RecordActionBody = {};
  const reason = input.reason === undefined || input.reason === null ? "" : input.reason.trim();
  if (reason !== "") body.reason = reason;
  if (input.commitAttempted === true) {
    if (input.confirmed !== true) {
      throw new Error("A commit-attempted record needs the destination check confirmed first.");
    }
    if (input.operatorId === null || input.operatorId === "") {
      throw new Error("A commit-attempted record needs the API's operator identity.");
    }
    body.destination_checked_by = input.operatorId;
  }
  return body;
}

export interface ApprovalBodyInput {
  requestId: string;
  decision: ApprovalDecision;
  operatorId: string | null;
  reason?: string | null;
}

export function approvalBody(input: ApprovalBodyInput): ApprovalDecisionBody {
  if (input.operatorId === null || input.operatorId === "") {
    throw new Error("Answering an approval needs the API's operator identity.");
  }
  const body: ApprovalDecisionBody = {
    request_id: input.requestId,
    decision: input.decision,
    decided_by: input.operatorId,
  };
  const reason = input.reason === undefined || input.reason === null ? "" : input.reason.trim();
  if (reason !== "") body.reason = reason;
  return body;
}

export interface Timers {
  setInterval: (handler: () => void, timeout: number) => unknown;
  clearInterval: (handle: unknown) => void;
}

/** The real clock, swappable so a test can drive it. */
export const DEFAULT_TIMERS: Timers = {
  setInterval: (handler, timeout) => setInterval(handler, timeout) as unknown,
  clearInterval: (handle) => {
    clearInterval(handle as ReturnType<typeof setInterval>);
  },
};

export interface Poller {
  start: () => void;
  stop: () => void;
  isRunning: () => boolean;
}

export interface PollerOptions {
  onTick: () => void;
  shouldContinue: () => boolean;
  intervalMs?: number;
  timers?: Timers;
}

/**
 * A poll that stops itself as soon as it is no longer wanted. The interval is injectable and the
 * timer functions are looked up at call time, so fake timers work with it.
 */
export function createPoller(options: PollerOptions): Poller {
  const intervalMs = options.intervalMs ?? POLL_INTERVAL_MS;
  const timers = options.timers ?? DEFAULT_TIMERS;
  let handle: unknown = null;
  const stop = (): void => {
    if (handle === null) return;
    timers.clearInterval(handle);
    handle = null;
  };
  return {
    start: (): void => {
      if (handle !== null) return;
      handle = timers.setInterval(() => {
        options.onTick();
        if (options.shouldContinue() === false) stop();
      }, intervalMs);
    },
    stop,
    isRunning: (): boolean => handle !== null,
  };
}
