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
/** A row a person skipped after its search did not find the record. */
const SKIPPED: Outcome = { label: "Skipped", tone: "idle" };
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

/** The words under every "Chrome isn't connected" the app shows: where the click is (PL-106). */
export const CONNECT_CHROME = "Click the ProcessLens icon in Chrome's toolbar to connect.";

/**
 * Whether a row's reason says its search did not find the record.
 *
 * Two shapes reach a row, and both mean the same thing to the page:
 *
 * * ``Not found in <site>.`` -- the reason earlier recordings still carry (PL-106 item 7);
 * * ``No <column> called ‘<value>’ was found.`` -- the plain sentence PL-113 item 6 added, kept by
 *   T1's review (B2) while the outcome went back to a park.
 *
 * The row's outcome and its action both read this, so a new spelling has to be added here rather
 * than at each call site.
 */
export function isNotFoundReason(reason?: string | null): boolean {
  const text = (reason ?? "").trim();
  if (text.startsWith("Not found in")) return true;
  return /^No .+ called .+ was found\.$/.test(text);
}

/**
 * A row's outcome in plain words. Unknown states fall back to "Waiting".
 *
 * A `skipped` row has two causes, told apart by the reason the runner wrote: a search that found
 * nothing leaves nothing to change, so a row a person skipped reads "Skipped"; any other skip was
 * a record that was already there (PL-106 item 7). Until the person skips it, the row is `parked`
 * and reads "Needs your help".
 */
export function rowOutcome(state: string, reason?: string | null): Outcome {
  if (state === "skipped" && isNotFoundReason(reason)) {
    return SKIPPED;
  }
  return ROW_STATES[state] ?? WAITING;
}

/**
 * A queued run with no Chrome session is not waiting for the engine, it is waiting for the person
 * to open the extension (PL-033 requirement 4). `chromeAttached` comes from
 * `GET /api/v1/executor/session`; while it is not known yet, the plain status stands.
 */
export const WAITING_FOR_CHROME = `Waiting for Chrome. ${CONNECT_CHROME}`;

/**
 * A run's status in plain words.
 *
 * A finished run is only a clean finish when no row is left for a person: `counts` lets a
 * `completed` run say how many rows were parked or failed beside it (PL-106 item 4).
 */
export function runStatusWords(
  status: string,
  chromeAttached?: boolean,
  counts?: Record<string, number>,
): Outcome {
  if (status === "queued" && chromeAttached === false) {
    return { label: WAITING_FOR_CHROME, tone: "idle" };
  }
  if (status === "completed" && counts !== undefined) {
    const needYou = (counts.parked ?? 0) + (counts.failed ?? 0);
    if (needYou > 0) {
      return {
        label: `Finished — ${needYou} ${needYou === 1 ? "needs" : "need"} you`,
        tone: "help",
      };
    }
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
  // PL-112 item 3: a finished row says what happened, never the engine's own words. The old
  // spellings stay here for rows already stored with them.
  "assertion passed": "Added and checked.",
  "the destination has this commit": "Added and checked.",
  "added and checked": "Added and checked.",
  "steps completed": "Done.",
  done: "Done.",
  "the assertion did not pass": "The page didn't show it saved, so ProcessLens checked the site.",
  "the page didn't show it saved": "The page didn't show it saved, so ProcessLens checked the site.",
  "precheck passed": "Checked the row.",
  "checked the row": "Checked the row.",
  "commit write-ahead": "Saving it now.",
  "saving it now": "Saving it now.",
  dispatched: "Sent it to the site; checking it arrived.",
  "sent it; checking it arrived": "Sent it to the site; checking it arrived.",
  "no dispatch proof": "ProcessLens couldn't tell whether the click was sent.",
  "the commit command was never dispatched": "The click was never sent.",
  "the click was never sent.": "The click was never sent.",
  "the extension refused the commit": "ProcessLens stopped before sending it.",
  "processlens stopped before sending it.": "ProcessLens stopped before sending it.",
  "could not verify the save automatically; check the destination":
    "ProcessLens couldn't check whether it saved. Look on the site, then Dismiss it or Try again.",
  "the destination check found more than one match":
    "The site shows more than one match for this row.",
  "the site shows more than one match for this row":
    "The site shows more than one match for this row.",
  "nobody decided whether to save this row in time":
    "Nobody approved this row in time, so it was left for you.",
  "a reviewer turned this row down": "A reviewer turned this row down.",
  "the task ended without saying what happened":
    "The task finished without saying what happened to this row.",
  "the save's check did not pass": "The page didn't show it saved.",
  "the page did not show it saved": "The page didn't show it saved.",
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
  // A not-found reason is the row's whole explanation and names a host, so it is shown exactly as
  // written: the code-to-words fallback would turn the hyphen in "my-crm.example.com" into a
  // space. Every other reason keeps the fallback (PL-106 item 7).
  if (isNotFoundReason(key) && key.endsWith(".")) return key;
  // A code we have never seen must still not reach the screen as an identifier.
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
  // PL-104: the run page offers one button for this, right under the reason, so the sentence names
  // that button rather than telling a person to press Resume.
  window_hidden:
    "The ProcessLens window was hidden or minimized, so the site stopped drawing. Press Show it and continue.",
  executor_disconnected: `Chrome disconnected. ${CONNECT_CHROME} Then resume.`,
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

/**
 * The week's saved figure. A minute's rounding would print "0 m" beside a week of rows, so
 * anything under a minute is said in words instead (PL-106 item 5). Nothing saved at all stays
 * "0 m".
 */
export function savedFigureWords(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  if (total > 0 && total < 60) return "Under a minute";
  return durationWords(total / 60);
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

/*
 * PL-067: the site daily cap. The pause names the site and its limit in one sentence, and the
 * run page keeps its Resume disabled while the count stands: the owner's rule that the action
 * sits on the same screen, but a resume while the cap holds would just be refused.
 */

export interface SiteCap {
  origin: string;
  limit: number;
  used: number;
}

/** The cap's origin as a person says it: linkedin.com, not https://www.linkedin.com/. */
export function siteCapHost(origin: string): string {
  return origin.replace(/^\w+:\/\//, "").replace(/\/+$/, "").replace(/^www\./, "");
}

/** The sentence the run page shows for a `site_daily_cap` pause. */
export function siteCapWords(cap: SiteCap): string {
  return (
    `Paused: today's limit for ${siteCapHost(cap.origin)} ` +
    `(${cap.limit} rows) is reached. It can continue tomorrow.`
  );
}

/** Whether the cap still stands: the Resume button stays disabled while it does. */
export function siteCapReached(cap: SiteCap): boolean {
  return cap.used >= cap.limit;
}

/**
 * PL-068: the site is asking for a verification code while the run is paused.
 *
 * The heading is the page's own words, passed through from the extension; the sentence never
 * carries a code, a URL or an engine word. The code itself is supplied by the person, typed on
 * this screen, and never stored.
 */
export function codePromptWords(heading?: string | null): string {
  const where = (heading ?? "").trim();
  const asked = where
    ? `The site is asking for a verification code (${where}).`
    : "The site is asking for a verification code.";
  return `${asked} Enter the code it sent you.`;
}

/** The moment between pressing Continue and the work window reporting back. */
export const ENTERING_CODE = "Entering the code…";

/** PL-104: the one button for a run paused because the ProcessLens window was hidden. */
export const SHOW_AND_CONTINUE = "Show it and continue";

/** The work window could not find the code field: the person types it there and resumes. */
export const CODE_NOT_FOUND =
  "ProcessLens couldn't find where to type the code. Type it in the work window, then press Resume.";

/** PL-102: the offer, beside the code box, to read the code from the person's own Gmail. */
export const CODE_FROM_EMAIL_OFFER =
  "Did the site email you the code? ProcessLens can read it from your Gmail in this Chrome and type it for you. It reads only new sign-in codes and keeps nothing.";

/** PL-102: the button that says yes, for this run and every later one. */
export const CODE_FROM_EMAIL_BUTTON = "Read it from my Gmail";

/** PL-102: under the button, so the yes is not a surprise later. */
export const CODE_FROM_EMAIL_LATER =
  "From then on, ProcessLens does this by itself when a site emails a code.";

/** PL-102: while the work window looks in Gmail, which can take up to a minute. */
export const LOOKING_IN_GMAIL = "Looking in your Gmail for the code… this can take up to a minute.";

/** PL-107 item 12: while the panel's recorder reads a sign-in code from the person's Gmail. */
export const READING_CODE_FROM_GMAIL = "Reading your code from Gmail…";

/** PL-102: a code was typed but the site is still asking for one. */
export const CODE_STILL_ASKED =
  "The code was typed, but the site is still asking for one. Check the work window, then type the right code here.";

/** PL-102: why the code could not be read from email, in words a person can act on. */
export function codeFromEmailWords(reason: string): string {
  switch (reason) {
    case "none":
      return "No code email has arrived yet. Try again in a moment, or type the code here.";
    case "ambiguous":
      return "Two codes arrived and ProcessLens can't tell which one is this site's. Type the right one here.";
    case "no_gmail":
      return "ProcessLens couldn't open Gmail in this Chrome. Sign in to Gmail in this Chrome, or type the code here.";
    case "not_found":
      return CODE_NOT_FOUND;
    default:
      return "ProcessLens couldn't read the code. Type it here.";
  }
}

/**
 * PL-072: the Spreadsheets page's Word-document area. A document becomes a CSV, and the two ways
 * on are the download and the task a person already knows.
 */
export const ADD_WORD_DOCUMENT = "Add a Word document or PDF";
export const ADD_WORD_DOCUMENT_SENTENCE =
  "Choose a Word document or a PDF with real text (not a scan), and ProcessLens will read its table, fields or list into rows.";
export const READING_DOCUMENT = "Reading the document…";
export const DOWNLOAD_CSV = "Download CSV";
export const USE_FOR_A_TASK = "Use for a task";

/*
 * PL-076: a cleaned document carried to a task. The task list says which document's rows are
 * waiting, the run page uploads those rows through the normal file path, and an expired token
 * says so once, in the upload area, with the file picker still there.
 */
export function pickTaskForCsv(fileName: string): string {
  return `Pick the task to use ${fileName}'s rows with.`;
}

export const DOCUMENT_ROWS_GONE =
  "That document's rows are no longer here. Add the Word document again, or pick a file.";
