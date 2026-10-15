/**
 * The whole plain-words vocabulary. Every screen goes through this module, so the engine's words
 * never reach a person's eyes (DESIGN.md §2, requirement 7).
 *
 * The mapping is the ticket's table; a state the table does not know falls back to "Waiting", and a
 * reason it does not know is shown as it stands, because the runner writes those for people.
 */

export type Tone = "ok" | "help" | "live" | "idle";

export interface Outcome {
  label: string;
  tone: Tone;
}

const WAITING: Outcome = { label: "Waiting", tone: "idle" };
const CHECKING: Outcome = { label: "Checking…", tone: "live" };
const DONE: Outcome = { label: "Done", tone: "ok" };
const ALREADY_THERE: Outcome = { label: "Already there", tone: "idle" };
const NEEDS_HELP: Outcome = { label: "Needs your help", tone: "help" };
const DIDNT_WORK: Outcome = { label: "Didn't work", tone: "help" };

/* engine state  ->  on screen */
const ROW_STATES: Record<string, Outcome> = {
  pending: WAITING,
  claimed: WAITING,
  prechecked: WAITING,
  filling: WAITING,
  submitting: CHECKING,
  submitted_unverified: CHECKING,
  reconciling: CHECKING,
  verified: DONE,
  skipped: ALREADY_THERE,
  parked: NEEDS_HELP,
  failed: DIDNT_WORK,
};

const RUN_STATUSES: Record<string, Outcome> = {
  queued: { label: "Queued", tone: "idle" },
  running: { label: "Running", tone: "live" },
  paused: { label: "Paused", tone: "help" },
  completed: { label: "Finished", tone: "ok" },
  cancelled: { label: "Stopped", tone: "idle" },
  failed: DIDNT_WORK,
};

/** A row's outcome in plain words. Unknown states fall back to "Waiting". */
export function rowOutcome(state: string): Outcome {
  return ROW_STATES[state] ?? WAITING;
}

/** A run's status in plain words. */
export function runStatusWords(status: string): Outcome {
  return RUN_STATUSES[status] ?? WAITING;
}

/* the reasons the runner writes  ->  one plain sentence */
const REASONS: Record<string, string> = {
  "attempts exhausted": "It didn't work after three tries.",
  "already committed in an earlier run": "It was already added in an earlier run.",
  "run cancelled": "The run was stopped before this row was reached.",
  "dismissed by a person": "You dismissed this row.",
  duplicate: "It was already there.",
  "the option doesn't exist": "That answer isn't in the list on the site.",
  "a value is missing": "A value it needs is missing from the row.",
};

/** "some_reason_code" -> "Some reason code", so a code nobody taught us is still readable. */
function sentence(token: string): string {
  const words = token.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  if (words === "") return "";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The reason a row needs a person, in one plain sentence. */
export function plainReason(reason: string | null, state?: string): string {
  const key = (reason ?? "").trim();
  if (key === "") {
    return state === "skipped" ? "It was already there." : "Something went wrong.";
  }
  const known = REASONS[key.toLowerCase()];
  if (known !== undefined) return known;
  // The runner already writes its reasons for people, but a code we have never seen must still not
  // reach the screen as an identifier.
  const readable = sentence(key);
  return /\.$/.test(readable) ? readable : `${readable}.`;
}

/*
 * Every pause reason the PL-015 operator page already put into words, carried forward unchanged so
 * this rebuild is not a regression: `paused_by_operator` is what the runs API sets, `operator_paused`
 * is the older spelling, and both are accepted.
 */
const PAUSE_REASONS: Record<string, string> = {
  login_required: "Log in to the site in the bound tab, then resume.",
  challenge: "Solve the security challenge the site is showing in the bound tab, then resume.",
  approval_required: "An approval request is waiting for your decision, then resume.",
  awaiting_approval: "An approval request is waiting for your decision, then resume.",
  paused_by_operator: "You paused this run. Resume when you are ready.",
  operator_paused: "You paused this run. Resume when you are ready.",
  manual_review: "A row in the table below needs your decision, then resume.",
  rate_limited: "The site is rate limiting us. Wait a moment, then resume.",
  destination_unreachable: "The site could not be reached. Check it, then resume.",
};

/** A pause reason in one actionable sentence, or `null` when there is none. */
export function pauseReasonWords(reason: string | null): string | null {
  const key = (reason ?? "").trim().toLowerCase();
  if (key === "") return null;
  const known = PAUSE_REASONS[key];
  if (known !== undefined) return known;
  return `Paused: ${sentence(key)}. Sort that out, then resume.`;
}

/** The states a run is still working through: `queued`, `running` or `paused`. */
export function isOpenStatus(status: string | null): boolean {
  return status === "queued" || status === "running" || status === "paused";
}

/** Which buttons a run's status offers. A finished run offers none of the three. */
export function runActions(status: string): { pause: boolean; resume: boolean; cancel: boolean } {
  const open = isOpenStatus(status);
  return { pause: open, resume: status === "paused", cancel: open };
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/** When something happened, as a person would say it. */
export function relativeTime(iso: string | null, now: Date = new Date()): string {
  if (iso === null || iso.trim() === "") return "Never";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "Never";
  const seconds = Math.floor((now.getTime() - at.getTime()) / 1000);
  if (seconds < 60) return "Now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${plural(minutes, "minute")} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${plural(hours, "hour")} ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "Yesterday";
  return `${plural(days, "day")} ago`;
}

/** "6 h 12 m" — minutes as hours and minutes, never negative. */
export function durationWords(minutes: number): string {
  const total = Math.max(0, Math.round(minutes));
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  if (hours === 0) return `${rest} m`;
  if (rest === 0) return `${hours} h`;
  return `${hours} h ${rest} m`;
}

/** "About 52 minutes saved" (or "About 1 hour saved"). */
export function minutesSavedWords(minutes: number): string {
  const total = Math.max(0, Math.round(minutes));
  if (total === 0) return "Nothing saved yet";
  if (total < 60) return `About ${plural(total, "minute")} saved`;
  const hours = Math.max(1, Math.round(total / 60));
  return `About ${plural(hours, "hour")} saved`;
}
