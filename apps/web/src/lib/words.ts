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

/**
 * A run that had finished and then had a row put back in the queue: the API reports it open again,
 * and the status says why it is open a second time (PL-038).
 */
export const RUNNING_AGAIN: Outcome = { label: "Running again", tone: "live" };

/** A row's outcome in plain words. Unknown states fall back to "Waiting". */
export function rowOutcome(state: string): Outcome {
  return ROW_STATES[state] ?? WAITING;
}

/**
 * A queued run with no Chrome session is not waiting for the engine, it is waiting for the person
 * to open the extension (PL-033 requirement 4). `chromeAttached` comes from
 * `GET /api/v1/executor/session`; while it is not known yet, the plain status stands.
 */
export const WAITING_FOR_CHROME =
  "Waiting for Chrome: open the ProcessLens extension and it will connect";

/** A run's status in plain words. */
export function runStatusWords(status: string, chromeAttached?: boolean): Outcome {
  if (status === "queued" && chromeAttached === false) {
    return { label: WAITING_FOR_CHROME, tone: "idle" };
  }
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
  login_required: "Log in to the site in the ProcessLens window, then resume.",
  challenge:
    "Solve the security challenge the site is showing in the ProcessLens window, then resume.",
  approval_required: "An approval request is waiting for your decision, then resume.",
  awaiting_approval: "An approval request is waiting for your decision, then resume.",
  paused_by_operator: "You paused this run. Resume when you are ready.",
  operator_paused: "You paused this run. Resume when you are ready.",
  manual_review: "A row in the table below needs your decision, then resume.",
  rate_limited: "The site is rate limiting us. Wait a moment, then resume.",
  destination_unreachable: "The site could not be reached. Check it, then resume.",
  // The runner's own reason for a run it paused at a step boundary because an admin froze runs
  // (PL-026). Without a sentence here the page would show the raw `admin_freeze` code.
  admin_freeze: "An admin paused all runs. Resume when an admin lets runs start again.",
  // PL-040: the reasons the runner sets that used to reach the page as "Paused: Canary failed".
  window_hidden:
    "The ProcessLens window was hidden behind other windows, so the site stopped drawing. Press Resume and it will come to the front.",
  executor_disconnected:
    "Chrome disconnected. Open the ProcessLens extension, press Connect, then resume.",
  canary_failed:
    "The first row didn't work, so the other rows are waiting. Check that row below, then resume.",
  policy_blocked: "Your company's Chrome settings block ProcessLens. Ask your IT team to allow it.",
  duplicate_detected:
    "A row looks like it may already be in the site. Check the row below, then resume.",
  records_not_exit: "A row stopped halfway. Check the rows below, then resume.",
  internal_error: "Something went wrong on our side. Resume to try again.",
};

/** A pause reason in one actionable sentence, or `null` when there is none. */
export function pauseReasonWords(reason: string | null): string | null {
  const key = (reason ?? "").trim().toLowerCase();
  if (key === "") return null;
  const known = PAUSE_REASONS[key];
  if (known !== undefined) return known;
  return `Paused: ${sentence(key)}. Sort that out, then resume.`;
}

/**
 * Every word the engine uses that a person must never read, with the DESIGN.md §2 wording each one
 * has to appear as instead. The engine-word test enforces this list, and `refusalWords` below is
 * built on the same list, so the rule the tests check and the rule the page applies cannot drift
 * apart.
 */
export const ENGINE_WORDS: ReadonlyArray<{ engine: string; pattern: RegExp; plain: string }> = [
  { engine: "recipe", pattern: /recipe/i, plain: "task" },
  { engine: "ledger", pattern: /ledger/i, plain: "never shown" },
  { engine: "parked", pattern: /parked/i, plain: "needs your help" },
  { engine: "record key", pattern: /record[\s_-]*key/i, plain: "the person's or thing's name" },
  { engine: "verified", pattern: /verified/i, plain: "added, updated or done" },
  { engine: "rung", pattern: /rung/i, plain: "never shown" },
  { engine: "idempotency", pattern: /idempotency/i, plain: "never shown" },
  // Whole tokens only, so an ordinary word or an id that merely contains the letters is not a hit.
  { engine: "v1", pattern: /\bv1\b/i, plain: "never shown" },
  { engine: "v2", pattern: /\bv2\b/i, plain: "never shown" },
  { engine: "step_", pattern: /step_/i, plain: "one plain sentence" },
  { engine: "fill-", pattern: /fill-/i, plain: "the element's own words" },
  { engine: "click-", pattern: /click-/i, plain: "the element's own words" },
];

/** The engine words this text contains, so a caller can name the rule it would break. */
export function engineWordsIn(text: string): string[] {
  return ENGINE_WORDS.filter((rule) => rule.pattern.test(text)).map((rule) => rule.engine);
}

/** The API refused a request, and it had nothing a person could use to say why. */
export const REFUSAL_FALLBACK = "The API refused that. Reload the page and try again.";

/** The row moved on between the page loading and the button being pressed. */
export const DEALT_WITH =
  "Someone else has already dealt with this row. Reload the page to see where it stands.";

const ALREADY_DEALT_WITH: readonly RegExp[] = [
  /is not parked or failed/i,
  /is not parked/i,
  /does not exist/i,
  /not the expected state/i,
  /illegal ledger transition/i,
];

/**
 * Said once when `GET /api/v1/operator` refuses: actions stay off until an operator exists.
 *
 * The API's own 503 detail is written for an engineer -- it names `PROCESSLENS_OPERATOR_ID` -- and
 * a person setting up the page is not one (ticket PL-029, fix 3). The words are fixed here, once,
 * for both screens that need them, so the wording cannot drift between them.
 */
export const OPERATOR_NOTE =
  "Actions are turned off until an operator is set up. Ask your admin.";

/**
 * The whole operator problem in one sentence, whatever the API's `detail` said.
 *
 * The detail is never shown: the sentence above is what a person can act on.
 */
export function operatorNote(_detail: string | null | undefined): string {
  return OPERATOR_NOTE;
}

/**
 * A refusal, in the words a person can act on.
 *
 * The API's own `detail` is written for an engineer: it names a state, `run_record`, or
 * `commit_attempted`, and DESIGN.md §2 and requirement 7 keep every one of those off the screen.
 * The translation lives here, once, rather than in each page -- and the last word belongs to
 * `engineWordsIn`, so a sentence that still carries an engine word is never shown whatever shape
 * the API happens to use next.
 */
export function refusalWords(detail: string | null | undefined): string {
  const text = (detail ?? "").trim();
  if (text === "") return REFUSAL_FALLBACK;
  if (ALREADY_DEALT_WITH.some((shape) => shape.test(text))) return DEALT_WITH;
  if (engineWordsIn(text).length > 0) return REFUSAL_FALLBACK;
  return text;
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

/**
 * The words the workflow editor's own sentences must never carry (PL-062).
 *
 * A step's own check and the way an element is found are engine ideas. The editor says "how we know
 * it saved" and "what the step clicks"; these two words never reach a person's eyes. They live in
 * their own list, appended here, so this file's other editor never has to touch `ENGINE_WORDS`.
 */
export const EDITOR_ENGINE_WORDS: ReadonlyArray<{
  engine: string;
  pattern: RegExp;
  plain: string;
}> = [
  { engine: "assert", pattern: /\bassert\w*/i, plain: "how we know it saved" },
  { engine: "selector", pattern: /\bselector\w*/i, plain: "the element's own words" },
];

/**
 * PL-061: what one row costs a task that never measured itself. The same three minutes the screens
 * used to assume for every row, now only the fallback.
 */
export const ESTIMATED_MANUAL_SECONDS_PER_ROW = 180;

/** "14 s", "2 min 10 s", "3 min" -- a short stretch of time, as a person says it. */
export function secondsWords(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes === 0) return `${rest} s`;
  if (rest === 0) return `${minutes} min`;
  return `${minutes} min ${rest} s`;
}

/**
 * The one comparison line on a finished run's results: what a row costs by hand, what ProcessLens
 * costs, and what the difference saved. When the task never measured itself, the line says so
 * rather than pretending the three-minute fallback was measured.
 */
export function timeComparisonWords(input: {
  manualSecondsPerRow: number | null | undefined;
  automatedSecondsPerRow: number | null | undefined;
  savedSeconds: number | null | undefined;
}): string {
  const manual = input.manualSecondsPerRow;
  if (manual === null || manual === undefined) {
    return `By hand: about ${secondsWords(ESTIMATED_MANUAL_SECONDS_PER_ROW)} a row (estimated)`;
  }
  const parts = [`By hand: about ${secondsWords(manual)} a row`];
  if (input.automatedSecondsPerRow !== null && input.automatedSecondsPerRow !== undefined) {
    parts.push(`ProcessLens: ${secondsWords(input.automatedSecondsPerRow)} a row`);
  }
  if (input.savedSeconds !== null && input.savedSeconds !== undefined) {
    parts.push(minutesSavedWords(input.savedSeconds / 60));
  }
  return parts.join(" · ");
}

/**
 * "estimated for 12 rows", or nothing at all when every row was measured. Home shows this under
 * the week's figure so an estimate is never presented as a measurement.
 */
export function estimatedRowsNote(rows: number | null | undefined): string | null {
  if (rows === null || rows === undefined || !Number.isFinite(rows) || rows <= 0) {
    return null;
  }
  const count = Math.round(rows);
  return `estimated for ${plural(count, "row")}`;
}

/**
 * PL-072: the Spreadsheets page's Word-document area. A document becomes a CSV, and the two ways
 * on are the download and the task a person already knows.
 */
export const ADD_WORD_DOCUMENT = "Add a Word document";
export const ADD_WORD_DOCUMENT_SENTENCE =
  "Choose a Word document and ProcessLens will read its table or list into rows.";
export const READING_DOCUMENT = "Reading the document…";
export const DOWNLOAD_CSV = "Download CSV";
export const USE_FOR_A_TASK = "Use for a task";
