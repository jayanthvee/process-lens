// "Save as task": the logic behind the popup form that turns the worker's
// recording into a compiled task, plus the small form object itself.
//
// The form is handed its root element, a storage area and a hub URL, so the
// compile request and the storage contract can be tested without a browser.
//
// Every sentence a person reads is spelled out here rather than built from an id:
// the engine's words (DESIGN.md §2) never reach the screen.
import { accessHeaders } from "../shared/access";
import type { RecordedEvent, RecordedStep } from "../shared/recipe-types";
import {
  DUPLICATES_BLOCKED,
  GO_WHY_NO_KEY,
  GO_WHY_NO_VALUES,
  GO_WHY_NOTHING_RECORDED,
  UNNAMED_CLICK,
  UNNAMED_STEP,
  plainSaveNotes,
  plainSentences,
} from "./words";
import { taskUrl } from "./flow";

/** Where the compiled recipe and its sample row live for the Run button. */
export const RECIPE_KEY = "processlens:executor-recipe";
export const ROW_KEY = "processlens:executor-row";

const COMPILE_PATH = "/api/v1/recipes/compile";
const NO_EVENTS =
  "Nothing has been recorded yet. Stop teaching first, then save it as a task.";

/**
 * The two fragments the e2e harness watches for, in one place.
 *
 * `tests/e2e/conftest.py`'s `compile_via_popup` waits until `#make-result` carries `is ready` or
 * `Could not`, and reads "is ready" as the only success. They are constants here so the popup and
 * the guard that checks them cannot drift apart, and the in-flight sentence is deliberately
 * neither.
 */
export const SAVED_READY = "is ready";
export const REFUSAL_HEADLINE = "Could not";
/** Shown while the request is out: it must look like neither an outcome nor a refusal. */
export const SAVING_NOTE = "Turning your recording into a task…";
/**
 * Where a compiled task is saved (PL-030): the dashboard only lists tasks ProcessLens holds, so
 * a successful compile is followed by this POST on the same ProcessLens address.
 */
const SAVE_TASK_PATH = "/api/v1/tasks";
/** The saved line's button text: the task's page opens in a real tab (PL-033 requirement 2). */
export const SAVED_OPEN = "Open in ProcessLens";
/** The prefix a failed save shows. The frozen contract needs "Could not"; this says more. */
export const SAVE_TASK_REFUSAL = "Could not save the task";
/** Shown under the duplicate checkbox when the recording holds no search at all: no fill of a
 *  column's own value was followed by the step that runs a search. PL-033 requirement 3's own
 *  words, so the save result says what is missing and what it costs. */
export const NO_SEARCH_HINT = DUPLICATES_BLOCKED;

/**
 * The two wordings of the search rule's checkbox (PL-077).
 *
 * A search that finds a record means two different things. For a create it means the record is
 * already there, and the row is skipped. For a find-then-update -- the search finds the record the
 * row is about to change -- skipping a found record would skip every row, and the case worth
 * naming is the opposite one: a search that finds *nothing* leaves nothing to change, so that row
 * is skipped too (PL-106). The compiler decides which shape a recording is and the form words the
 * rule to match.
 */
export const SKIP_IF_THERE = "If this row is already there, skip it";
export const PARK_WHEN_NOTHING_FOUND = "Skip a row when the search finds nothing";

/** The checkbox's wording for a compiled shape: no shape, or a create, keeps today's words. */
export function duplicateLabelFor(searchKind: string | null): string {
  return searchKind === "update" ? PARK_WHEN_NOTHING_FOUND : SKIP_IF_THERE;
}

/** One recorded constant value, ready to become a row column. */
export interface RecordedValue {
  stepId: string;
  column: string;
  value: string;
  label: string;
  required: boolean;
}

/** The body `POST /api/v1/recipes/compile` accepts. */
export interface CompileRequestBody {
  recording: RecordedEvent[];
  name: string;
  when_to_use: string;
  sample_row: Record<string, string>;
  record_key_column: string;
  allowed_origins: string[];
  commit_step_ids: string[];
  /** PL-017: propose the duplicate-search branch. */
  duplicate_check: boolean;
  /** PL-017: columns whose emptiness parks the record before any step runs. */
  park_fields: string[];
}

/** What the compile route answered: a recipe, or the problems it found. */
export type CompileOutcome =
  | {
      ok: true;
      recipe: Record<string, unknown>;
      warnings: string[];
      /** PL-077: `"update"`, `"create"`, or null when the recording holds no search. */
      searchKind: string | null;
    }
  | { ok: false; problems: string[] };

/** The sentence `recordedWarnings` adds when a recording saves twice (PL-042). */
export const REPEAT_SAVE_NOTE =
  "This recording saves twice. ProcessLens kept the first entry and ignored the repeat after it.";

/** The value a step writes, or null when the step writes nothing recorded. */
function constantValue(step: RecordedStep): string | null {
  if (step.action !== "fill" && step.action !== "select") return null;
  const source = step.value;
  if (!source || source.kind !== "constant") return null;
  // Values become row cells, so a number or boolean is stringified here.
  return String(source.value);
}

/** The field's human name, climbing the target ladder's named rungs. */
function humanName(step: RecordedStep): string | null {
  const targets = step.targets ?? [];
  for (const target of targets) {
    if (target.by === "role" && target.name.trim().length > 0) return target.name.trim();
  }
  for (const target of targets) {
    if (target.by === "label" && target.text.trim().length > 0) return target.text.trim();
  }
  for (const target of targets) {
    if (target.by === "testid" && target.value.trim().length > 0) return target.value.trim();
  }
  const name = step.name?.trim();
  return name && name.length > 0 ? name : null;
}

/**
 * A recipe field name: lowercased, runs of anything outside [a-z0-9] collapsed to
 * `_`, trimmed of leading/trailing `_`, prefixed `f_` when it would not start with a
 * letter, and cut to the schema's 64-character limit. An entirely empty name stays
 * empty so `allocate`'s "value" fallback still applies.
 */
function slug(text: string): string {
  const ascii = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (ascii.length === 0) return "";
  const prefixed = /^[a-z]/.test(ascii) ? ascii : `f_${ascii}`;
  return prefixed.slice(0, 64);
}

/** Hands out `name`, then `name_2`, `name_3` as columns repeat, within 64 chars. */
function allocate(base: string, taken: Map<string, number>): string {
  const first = base.length > 0 ? base : "value";
  const seen = taken.get(first) ?? 0;
  taken.set(first, seen + 1);
  if (seen === 0) return first;
  const suffix = `_${seen + 1}`;
  return `${first.slice(0, 64 - suffix.length)}${suffix}`;
}

/**
 * A comparable rung of a click's target ladder: the role/name rung when the ladder has one,
 * else the first css rung, else nothing. Two clicks that name the same control the same way
 * carry the same signature; PL-042 uses it to spot a repeated save.
 */
function targetSignature(step: RecordedStep): string | null {
  for (const target of step.targets ?? []) {
    if (target.by === "role") {
      return `role:${target.role.toLowerCase()}:${target.name.trim().toLowerCase()}`;
    }
  }
  for (const target of step.targets ?? []) {
    if (target.by === "css") return `css:${target.selector}`;
  }
  return null;
}

/**
 * The events up to and including the first entry's save click (PL-042).
 *
 * A recording saves twice when the click chosen as the save step names the same control as
 * another click in the recording: the person demonstrated one entry, saved it, and carried on
 * to record a second entry the row must not hold. Everything after the first save click is
 * dropped; a recording that saves once comes back unchanged, as does one with no chosen step.
 */
export function firstEntryEvents(
  events: RecordedEvent[],
  commitStepId: string | null,
): RecordedEvent[] {
  if (!commitStepId) return events;
  const commitEvent = events.find(
    (event) =>
      event?.step != null && event.step.id === commitStepId && event.step.action === "click",
  );
  const commit = commitEvent?.step ?? null;
  if (!commit) return events;
  const signature = targetSignature(commit);
  if (!signature) return events;
  let first = -1;
  let saves = 0;
  events.forEach((event, index) => {
    const step = event?.step ?? null;
    if (!step || step.action !== "click") return;
    // T1: the recorder logs a form save as a click and then a `submit` of the same control; the
    // compiler folds the pair into one step, so the `submit` is not a second save.
    if (event.kind === "submit") return;
    if (targetSignature(step) === signature) {
      if (first === -1) first = index;
      saves += 1;
    }
  });
  if (saves < 2 || first === -1) return events;
  // T1: the save's own submit and observation (its confirmation, recorded right after the click) belongs to
  // the first entry. Cutting it off leaves the commit with nothing to check, so the first row
  // parks with "could not verify the save".
  let end = first + 1;
  while (
    end < events.length &&
    (events[end]?.kind === "observation" || events[end]?.kind === "submit")
  ) {
    end += 1;
  }
  return events.slice(0, end);
}

/**
 * The events the popup's readers see: the first entry when the recording saves twice
 * (PL-042), everything otherwise. The save step is the recording's own default commit.
 */
function entryEvents(events: RecordedEvent[]): RecordedEvent[] {
  return firstEntryEvents(events, defaultCommitStepId(events));
}

/**
 * Every distinct value the recording typed or chose, in recording order.
 *
 * A `fill`/`select` that carried a non-constant source is skipped: the compiler's
 * row can only hold what was actually demonstrated. A fill the envelope marks as
 * `supersedes`d contributes nothing either: it is the typed prefix an accepted
 * suggestion replaced, so the chip fill that absorbed it is the value and the
 * column the person wants to see -- the popup and the compiler must not disagree.
 */
export function recordedValues(events: RecordedEvent[]): RecordedValue[] {
  // PL-042: a recording that saves twice offers only its first entry's values.
  events = entryEvents(events);
  const values: RecordedValue[] = [];
  // PL-078: one column per *field* and value. Two different fields taught the same value (a
  // timesheet's Monday and Tuesday are both "2:00") are two columns; the same field recorded twice
  // with one value (a chip fill, a page-set repeat) is still one.
  const seenFields = new Set<string>();
  const takenColumns = new Map<string, number>();

  // A fill another event's envelope names in `supersedes` is the typed prefix an accepted
  // suggestion replaced; the chip fill that absorbed it is the value and the column to offer.
  const superseded = new Set<string>();
  for (const event of events) {
    if (event?.supersedes) superseded.add(event.supersedes);
  }

  // PL-037: when the page fills a field and the person then edits it (Odoo copies a picked
  // contact's email; the owner typed their own over it), those back-to-back fills of that field
  // are one entry and only its last value counts. Offering a half-typed value as a column of its
  // own made it the row's key, and the compile then had no step that fills it. Back-to-back fills
  // the PAGE did not start stay separate entries, as before.
  const firstRung = (event: RecordedEvent | undefined): string =>
    JSON.stringify(event?.step?.targets?.[0] ?? null);
  const rewritten = new Set<number>();
  let chainFromPage = false;
  events.forEach((event, index) => {
    const previous = events[index - 1];
    const sameAsPrevious =
      event?.kind === "fill" && previous?.kind === "fill" && firstRung(event) === firstRung(previous);
    if (!sameAsPrevious) chainFromPage = event?.kind === "fill" && event.page_set === true;
    const next = events[index + 1];
    if (
      chainFromPage &&
      event?.kind === "fill" &&
      next?.kind === "fill" &&
      firstRung(event) === firstRung(next)
    ) {
      rewritten.add(index);
    }
  });

  for (const [index, event] of events.entries()) {
    if (rewritten.has(index)) continue;
    const step = event?.step ?? null;
    if (!step) continue;
    if (step.id !== undefined && superseded.has(String(step.id))) continue;
    // A value the page set on its own is not something the person demonstrated, so it is not a
    // column a row should fill. The form still shows the recorder's sentence about it
    // (`recordedWarnings`), because a person reviewing the task should know it happened.
    if (event.page_set === true) continue;
    const value = constantValue(step);
    if (value === null) continue;
    if (seenFields.has(`${JSON.stringify(step.targets ?? [])}|${value}`)) continue;
    seenFields.add(`${JSON.stringify(step.targets ?? [])}|${value}`);

    const name = humanName(step);
    const column = allocate(slug(name ?? String(step.id ?? "")), takenColumns);
    values.push({
      stepId: String(step.id ?? ""),
      column,
      value,
      label: name ?? column,
      required: event.required === true,
    });
  }
  return values;
}

/**
 * The recorder's own caveats about the recording, one sentence per event that carries one.
 * The form shows these so a person sees a step that rests on a positional selector before
 * they compile, rather than after a replay moves the layout out from under it. An
 * `unrecordable` event has no step and still has a warning, so the label falls back to plain words.
 *
 * PL-042: when the recording saves twice, the repeat after the first save click is ignored
 * -- and said so, in the same list, so a person is told where the `_2` column went.
 */
export function recordedWarnings(events: RecordedEvent[]): string[] {
  const entry = entryEvents(events);
  const warnings: string[] = [];
  for (const event of entry) {
    const warning = event?.warning;
    if (!warning) continue;
    warnings.push(`${stepWords(event.step ?? null)}: ${warning}`);
  }
  if (entry.length < events.length) warnings.push(REPEAT_SAVE_NOTE);
  return warnings;
}

/**
 * What to call a step in a sentence: the element's own words, never its id.
 *
 * A step the recorder could not name at all is named by what it is ("This click"), and a warning
 * on an event with no step at all (an `unrecordable`) says so instead of naming an id.
 */
function stepWords(step: RecordedStep | null): string {
  if (!step) return UNNAMED_STEP;
  const name = humanName(step);
  if (name) return name;
  if (step.action === "click") return UNNAMED_CLICK;
  if (step.action === "press") return "This key";
  return "This field";
}

/** Something that looks enough like an email address to identify a record. */
const EMAIL_VALUE = /^[^@\s]+@[^@\s]+$/;

/**
 * Whether a recorded step's own target says "search": a searchbox role, or a name/value/text/within
 * that mentions search. Mirrors the compiler's check so the form and the compiler agree on what a
 * search is (a wizard's fill -> Next is not one).
 */
function looksLikeSearch(step: RecordedStep | null): boolean {
  if (!step) return false;
  for (const target of step.targets ?? []) {
    if (target.by === "role" && target.role.toLowerCase() === "searchbox") return true;
    if (target.within && target.within.toLowerCase().includes("search")) return true;
    for (const text of [
      "name" in target ? target.name : "",
      "value" in target ? target.value : "",
      "text" in target ? target.text : "",
    ]) {
      if (text.toLowerCase().includes("search")) return true;
    }
  }
  return false;
}

/**
 * Whether one recorded value was searched for: a fill carrying it, immediately followed by the
 * step that runs the search. A search bar that applies on Enter -- Odoo's own search view, and most
 * application search bars -- has no button to click, so Enter counts as running it.
 */
function searchesForValue(events: RecordedEvent[], value: string): boolean {
  for (let index = 0; index < events.length - 1; index += 1) {
    const step = events[index]?.step ?? null;
    if (!step || step.action !== "fill") continue;
    if (constantValue(step) !== value) continue;
    const next = events[index + 1]?.step ?? null;
    if (!next) continue;
    const runs =
      next.action === "click" || (next.action === "press" && next.key === "Enter");
    if (runs && (looksLikeSearch(step) || looksLikeSearch(next))) return true;
  }
  return false;
}

/**
 * Whether the recording searches the destination for a value it can identify a record by.
 *
 * The key column is preferred, but it is not the only column that can carry a duplicate check: a
 * workflow keyed on a lead's name is still searched by email, and the check has to read whatever
 * the destination was actually searched for. The key column must still be one of the recording's
 * own columns, or there is nothing to check a row against. Returns false when no key column is
 * chosen.
 */
export function searchDetected(events: RecordedEvent[], recordKeyColumn: string): boolean {
  // PL-042: a repeat entry after the first save click is not part of the workflow.
  events = entryEvents(events);
  const column = recordKeyColumn.trim();
  if (column.length === 0) return false;
  const values = recordedValues(events);
  if (!values.some((value) => value.column === column)) return false;
  const searched = values.find((value) => value.column === column);
  if (searched && searchesForValue(events, searched.value)) return true;
  return values.some(
    (value) => value.column !== column && searchesForValue(events, value.value),
  );
}

/**
 * The park-field columns to pre-tick: every column whose recorded value is required, plus the
 * record key column when one is chosen. Order follows the columns; the key is appended last.
 */
export function parkFieldDefaults(events: RecordedEvent[], recordKeyColumn: string): string[] {
  const defaults: string[] = [];
  for (const value of recordedValues(events)) {
    if (value.required && !defaults.includes(value.column)) defaults.push(value.column);
  }
  const key = recordKeyColumn.trim();
  if (key.length > 0 && !defaults.includes(key)) defaults.push(key);
  return defaults;
}

/**
 * The column the record-key select should offer first: an email-named field when
 * there is one, else a value that looks like an email, else nothing. The recorder's
 * buffering can put a non-identifying field first, so it is never the default.
 */
export function defaultRecordKeyColumn(events: RecordedEvent[]): string | null {
  const values = recordedValues(events);
  for (const value of values) {
    if (/email/i.test(value.column) || /email/i.test(value.label)) return value.column;
  }
  for (const value of values) {
    if (EMAIL_VALUE.test(value.value)) return value.column;
  }
  return null;
}

/**
 * The step the form should preselect as the commit: the last recorded click.
 *
 * A form submit carries the click step for its submit control, so a click step is
 * accepted whatever event kind brought it.
 */
export function defaultCommitStepId(events: RecordedEvent[]): string | null {
  let last: string | null = null;
  for (const event of events) {
    const step = event?.step ?? null;
    if (step && step.action === "click" && step.id) last = step.id;
  }
  return last;
}

/** Every recorded step that can be the commit, labelled by its own words for the select. */
export function commitChoices(events: RecordedEvent[]): { id: string; label: string }[] {
  const choices: { id: string; label: string }[] = [];
  for (const event of events) {
    const step = event?.step ?? null;
    if (!step || step.action !== "click" || !step.id) continue;
    // The value stays the step id -- that is what `commit_step_ids` sends. Only the label a
    // person reads is plain words, and never the id.
    choices.push({ id: step.id, label: humanName(step) ?? UNNAMED_CLICK });
  }
  return choices;
}

/** The distinct origins the recording visited, in recording order. */
export function recordedOrigins(events: RecordedEvent[]): string[] {
  const origins: string[] = [];
  for (const event of events) {
    try {
      const origin = new URL(event?.url ?? "").origin;
      if (origin && origin !== "null" && !origins.includes(origin)) origins.push(origin);
    } catch {
      // A recording may carry a URL the browser cannot parse; it contributes nothing.
    }
  }
  return origins;
}

export function makeRecipeRequest(
  events: RecordedEvent[],
  choice: {
    name: string;
    recordKeyColumn: string;
    commitStepId: string | null;
    allowedOrigins?: string[];
    duplicateCheck?: boolean;
    parkFields?: string[];
  },
): CompileRequestBody {
  // PL-042: the recording sent is the first entry when the recording saves twice; the chosen
  // commit names the save step, falling back to the recording's own default.
  const entry = firstEntryEvents(events, choice.commitStepId ?? defaultCommitStepId(events));
  // T1 review: a trimmed recording ends at the FIRST save click, so that click is the commit. The
  // chosen id may name the repeat's save, which is no longer in the recording -- left as is, the
  // first save would compile as a plain click with no write-ahead and no save check.
  const trimmed = entry.length < events.length;
  const commitStepId = trimmed
    ? ([...entry].reverse().find((event) => event?.step?.action === "click")?.step?.id ??
      choice.commitStepId)
    : choice.commitStepId;
  const sampleRow: Record<string, string> = {};
  for (const value of recordedValues(entry)) sampleRow[value.column] = value.value;

  return {
    recording: entry,
    name: choice.name,
    // This ticket's form has no separate "when to use" field, and the route needs
    // one, so the workflow name doubles as it until a later ticket adds the field.
    when_to_use: choice.name,
    sample_row: sampleRow,
    record_key_column: choice.recordKeyColumn,
    // The form pins the origins the person ticked; a direct caller that names none
    // gets the recording's own origins, exactly as before this field existed.
    allowed_origins: choice.allowedOrigins ?? recordedOrigins(entry),
    commit_step_ids: commitStepId ? [commitStepId] : [],
    // The route defaults these the same way, so a caller that names neither stays
    // on the compiler's built-in defaults.
    duplicate_check: choice.duplicateCheck ?? true,
    park_fields: choice.parkFields ?? [],
  };
}

async function readJson(response: { json(): Promise<unknown> }): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

/** What the tasks route answered: where the saved task lives, or the reasons it refused. */
export type SaveTaskOutcome =
  | { ok: true; workflowId: string }
  | { ok: false; problems: string[] };

/** The body `POST /api/v1/tasks` accepts. */
export interface SaveTaskBody {
  name: string;
  recipe: Record<string, unknown>;
}

/** Turns the route's error body into sentences a person can read. */
function problemsFrom(status: number, body: unknown): string[] {
  const detail = (body as { detail?: unknown } | null | undefined)?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const problems = (detail as { problems?: unknown }).problems;
    if (Array.isArray(problems)) {
      const sentences = problems.filter(
        (problem): problem is string => typeof problem === "string" && problem.trim().length > 0,
      );
      if (sentences.length > 0) return sentences;
    }
  }
  if (typeof detail === "string" && detail.trim().length > 0) return [detail.trim()];
  // FastAPI's validation errors arrive as an array of objects, and any other shape
  // is not ours to interpret: name the status code instead of guessing at it.
  return [`ProcessLens would not accept this recording (HTTP ${status}).`];
}

/**
 * Sends one recording to the compile route. An HTTP error becomes a
 * `{ok: false}` outcome; only a network failure is allowed to throw.
 */
export async function compileRecording(
  request: CompileRequestBody,
  deps: { fetchImpl?: typeof fetch; baseUrl: string; accessCode?: string },
): Promise<CompileOutcome> {
  const fetchImpl = deps.fetchImpl ?? fetch;
  const baseUrl = deps.baseUrl.replace(/\/+$/, "");
  const response = await fetchImpl(`${baseUrl}${COMPILE_PATH}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...accessHeaders(deps.accessCode) },
    body: JSON.stringify(request),
  });

  const body = await readJson(response);
  if (!response.ok) return { ok: false, problems: problemsFrom(response.status, body) };

  const payload = (body ?? {}) as {
    recipe?: unknown;
    warnings?: unknown;
    search_kind?: unknown;
  };
  const recipe = payload.recipe;
  if (!recipe || typeof recipe !== "object") {
    return {
      ok: false,
      problems: [`ProcessLens answered but sent no task back (HTTP ${response.status}).`],
    };
  }
  const warnings = Array.isArray(payload.warnings)
    ? payload.warnings.filter(
        (warning): warning is string => typeof warning === "string" && warning.length > 0,
      )
    : [];
  // PL-077: the compiler says which searched shape it compiled, so the form can word its rule.
  const searchKind =
    typeof payload.search_kind === "string" && payload.search_kind.length > 0
      ? payload.search_kind
      : null;
  return { ok: true, recipe: recipe as Record<string, unknown>, warnings, searchKind };
}

/**
 * Saves the compiled task: `POST /api/v1/tasks` with the name and the recipe (PL-030).
 *
 * Compiling stores nothing, so this is the call that makes the task real -- the one that puts it
 * on the dashboard. An HTTP error becomes a `{ok: false}` outcome; only a network failure is
 * allowed to throw, exactly like the compile.
 */
export async function saveTask(
  request: SaveTaskBody,
  deps: { fetchImpl?: typeof fetch; baseUrl: string; accessCode?: string },
): Promise<SaveTaskOutcome> {
  const fetchImpl = deps.fetchImpl ?? fetch;
  const baseUrl = deps.baseUrl.replace(/\/+$/, "");
  const response = await fetchImpl(`${baseUrl}${SAVE_TASK_PATH}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...accessHeaders(deps.accessCode) },
    body: JSON.stringify(request),
  });

  const body = await readJson(response);
  if (!response.ok) return { ok: false, problems: problemsFrom(response.status, body) };
  const workflowId = (body as { workflow_id?: unknown } | null | undefined)?.workflow_id;
  if (typeof workflowId !== "string" || workflowId.length === 0) {
    return {
      ok: false,
      problems: [`ProcessLens answered but stored no task (HTTP ${response.status}).`],
    };
  }
  return { ok: true, workflowId };
}

/** The storage area the form writes the recipe and the row into. */
export interface FormStorage {
  get(keys: string[]): Promise<Record<string, unknown>>;
  set(items: Record<string, unknown>): Promise<void>;
}

export interface MakeRecipeFormDeps {
  root: ParentNode;
  storage: FormStorage;
  fetchImpl?: typeof fetch;
  baseUrl: string;
  /** PL-086: the team access code a hosted ProcessLens asks for; empty on a laptop. */
  accessCode?: string;
  onNotice?: (message: string) => void;
  /** PL-033: the saved task's address on the dashboard, for the Open in ProcessLens step. */
  onSaved?: (taskUrl: string) => void;
}

export interface MakeRecipeForm {
  refresh(events: RecordedEvent[]): void;
  submit(): Promise<void>;
}

function control<T extends Element>(root: ParentNode, scope: ParentNode, id: string): T {
  // A control the three-step flow moved out of the form's own section (the commit select under
  // More, the support notes) is still the form's to drive, so the page is searched too.
  const found = scope.querySelector<T>(`#${id}`) ?? root.querySelector<T>(`#${id}`);
  if (!found) throw new Error(`the make-recipe form is missing #${id}`);
  return found;
}

function option(value: string, text: string): HTMLOptionElement {
  const el = document.createElement("option");
  el.value = value;
  el.textContent = text;
  return el;
}

/** One origin's row: a checkbox, ticked, next to the origin it names. */
function originRow(origin: string): HTMLLabelElement {
  const row = document.createElement("label");
  row.className = "field__choice";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.value = origin;
  box.checked = true;
  const text = document.createElement("span");
  text.textContent = origin;
  row.append(box, text);
  return row;
}

/** One input column's park row: a checkbox carrying its column, ticked when empty should park. */
function parkRow(value: RecordedValue, checked: boolean): HTMLLabelElement {
  const row = document.createElement("label");
  row.className = "field__choice";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = checked;
  box.dataset.column = value.column;
  const text = document.createElement("span");
  text.textContent = `Stop and ask me if this is empty: ${value.label}`;
  row.append(box, text);
  return row;
}

/** A name to offer before the person types one of their own. */
function defaultName(events: RecordedEvent[]): string {
  try {
    const host = new URL(events[0]?.url ?? "").hostname;
    return host.length > 0 ? `Recorded on ${host}` : "Recorded task";
  } catch {
    return "Recorded task";
  }
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * The "Save as task" form over the ids in popup.html: `make-name`, `make-key`,
 * `make-commit`, `make-duplicate`, `make-duplicate-hint`, `make-origins`,
 * `make-park-fields`, `make-go`, `make-result` and `make-warnings`.
 *
 * `refresh` mirrors a recording into the controls and `submit` posts it; the
 * caller wires the button, so it can point `baseUrl` at the hub field first.
 */
export function createMakeRecipeForm(deps: MakeRecipeFormDeps): MakeRecipeForm {
  const scope = deps.root.querySelector<HTMLElement>("#make-recipe") ?? deps.root;
  const nameEl = control<HTMLInputElement>(deps.root, scope, "make-name");
  const keyEl = control<HTMLSelectElement>(deps.root, scope, "make-key");
  const commitEl = control<HTMLSelectElement>(deps.root, scope, "make-commit");
  const duplicateEl = control<HTMLInputElement>(deps.root, scope, "make-duplicate");
  const duplicateHintEl = control<HTMLParagraphElement>(deps.root, scope, "make-duplicate-hint");
  const originsEl = control<HTMLFieldSetElement>(deps.root, scope, "make-origins");
  const parkEl = control<HTMLFieldSetElement>(deps.root, scope, "make-park-fields");
  const goEl = control<HTMLButtonElement>(deps.root, scope, "make-go");
  // PL-106 item 1: the sentence under the button that says why it is greyed out. An older markup
  // without it still works; the button simply says nothing there.
  const goWhyEl = deps.root.querySelector<HTMLParagraphElement>("#make-go-why");
  const resultEl = control<HTMLParagraphElement>(deps.root, scope, "make-result");
  const warningsEl = control<HTMLParagraphElement>(deps.root, scope, "make-warnings");
  // The two plain sentences under the save result; a page without the element (an older markup)
  // simply shows none.
  const plainEl = deps.root.querySelector<HTMLParagraphElement>("#make-plain");

  let events: RecordedEvent[] = [];
  let busy = false;

  function notice(message: string): void {
    resultEl.textContent = message;
    deps.onNotice?.(message);
  }

  /**
   * The one sentence under the button, first reason winning (PL-106 item 1).
   *
   * The button is off for three reasons, and two of them are invisible on their own: an empty
   * recording, no value typed while teaching (so the key select has no options), and no key picked.
   * While a save is in flight the busy note under the result already says what is happening, so
   * this line goes quiet rather than competing with it.
   */
  function goWhyWords(): string {
    if (busy) return "";
    if (events.length === 0) return GO_WHY_NOTHING_RECORDED;
    if (recordedValues(events).length === 0) return GO_WHY_NO_VALUES;
    if (keyEl.value.length === 0) return GO_WHY_NO_KEY;
    return "";
  }

  /** The button follows the recording, the busy flag, and whether a key is chosen. */
  function syncGo(): void {
    goEl.disabled = events.length === 0 || busy || keyEl.value.length === 0;
    if (goWhyEl) goWhyEl.textContent = goWhyWords();
  }

  /**
   * The duplicate check follows the chosen key: it is offered only when the recording holds a
   * search for that key's value, and says so when the person has not recorded one.
   */
  function syncDuplicate(): void {
    const detected = searchDetected(events, keyEl.value);
    duplicateEl.disabled = !detected;
    duplicateEl.checked = detected;
    duplicateHintEl.textContent = detected ? "" : NO_SEARCH_HINT;
  }

  /** The checkbox's own sentence: the form's label, in the markup beside the box. */
  const duplicateLabelEl = duplicateEl
    .closest("label")
    ?.querySelector<HTMLElement>(".field__label");

  /**
   * Word the search rule for the shape the compiler just reported (PL-077).
   *
   * A create keeps "If this row is already there, skip it"; a find-then-update is told the truth
   * about what the same rule now does: it skips a row whose search found nothing to change.
   */
  function applySearchKind(searchKind: string | null): void {
    if (duplicateLabelEl) duplicateLabelEl.textContent = duplicateLabelFor(searchKind);
  }

  /**
   * One park checkbox per input column, pre-ticked for a required field or the record key. The
   * record key's row is not fixed at refresh time: the person chooses the key in the select, so
   * the key's own tick follows the select (a new key is ticked, the old one is not).
   */
  function renderParkFields(): void {
    const columns = recordedValues(events);
    const defaults = parkFieldDefaults(events, keyEl.value);
    const parkLegend = parkEl.querySelector("legend");
    const parkRows = columns.map((value) => parkRow(value, defaults.includes(value.column)));
    parkEl.replaceChildren(...(parkLegend ? [parkLegend] : []), ...parkRows);
  }

  /**
   * The two plain sentences under the save result (PL-033 requirement 3): whether duplicates can
   * be checked, and what stops to ask. Nothing about the engine, nothing about values the page
   * set itself, and never more than two sentences.
   */
  function renderPlainNotes(): void {
    if (!plainEl) return;
    const values = recordedValues(events);
    const labels = parkFieldDefaults(events, keyEl.value).map(
      (column) => values.find((value) => value.column === column)?.label ?? column,
    );
    const notes = plainSaveNotes({
      duplicatesBlocked: events.length > 0 && !searchDetected(events, keyEl.value),
      parkLabels: labels,
    });
    plainEl.textContent = notes.join(" ");
  }

  // A key the person picks re-evaluates the park ticks, the duplicate check, the plain notes and
  // the button.
  keyEl.addEventListener("change", () => {
    syncGo();
    syncDuplicate();
    renderParkFields();
    renderPlainNotes();
  });

  /**
   * A block of the engine's own sentences, kept for support exactly as the engine wrote them.
   *
   * PL-033: the person reads the save result and at most two plain sentences under it; these lines
   * are the rest, reachable under More → "Notes for support".
   */
  function supportBlock(sentences: string[]): string {
    return sentences.filter((line) => line.trim().length > 0).join("\n");
  }

  function showProblems(problems: string[]): void {
    // A rejection with no problems the route would own up to is still said in words.
    const sentences =
      problems.length > 0 ? problems : ["ProcessLens would not accept this recording."];
    // The count is the true one, whether or not a sentence is one this page can show.
    const count = sentences.length === 1 ? "1 problem" : `${sentences.length} problems`;
    notice(`Could not save this as a task (${count}).`);
    // PL-033: the refusal's own sentences are for support. The person reads the headline and the
    // two plain notes; everything else stays reachable under More → "Notes for support".
    warningsEl.textContent = sentences.join("\n");
  }

  /**
   * The first plain sentence among the save route's reasons, or one that owns the refusal.
   *
   * The tasks route refuses with the compiler's own sentences on a bad recipe and with one line on
   * anything else; the same filter that guards `#make-warnings` decides what a person reads here.
   */
  function plainReason(problems: string[]): string {
    const { shown } = plainSentences(problems.join("\n"));
    return shown[0] ?? "ProcessLens would not say what went wrong.";
  }

  /**
   * The saved task's own line: its name, that it is ready, and the flow's next step. There is no
   * link here on purpose: a plain `<a href>` does nothing in an extension popup, so the Open in
   * ProcessLens card calls `chrome.tabs.create` with the address instead (PL-033 requirement 2).
   */
  function showSaved(name: string, workflowId: string): void {
    resultEl.textContent = `${name} ${SAVED_READY}.`;
    deps.onNotice?.(`${name} ${SAVED_READY}.`);
    deps.onSaved?.(taskUrl(deps.baseUrl, workflowId));
  }

  function refresh(next: RecordedEvent[]): void {
    events = Array.isArray(next) ? next : [];
    // A recording that has not been compiled is not yet a shape: until the compiler says
    // otherwise, the rule is worded as the create's (PL-077).
    applySearchKind(null);

    const values = recordedValues(events);
    // A name the person typed survives a refresh; an empty box gets a default.
    if (nameEl.value.trim().length === 0) nameEl.value = defaultName(events);
    keyEl.replaceChildren(...values.map((value) => option(value.column, value.label)));
    keyEl.value = defaultRecordKeyColumn(events) ?? "";
    keyEl.disabled = values.length === 0;

    const choices = commitChoices(events);
    commitEl.replaceChildren(...choices.map((choice) => option(choice.id, choice.label)));
    commitEl.value = defaultCommitStepId(events) ?? "";
    commitEl.disabled = choices.length === 0;

    // Every origin starts ticked; the legend stays put when the recording has none.
    const legend = originsEl.querySelector("legend");
    const rows = recordedOrigins(events).map((origin) => originRow(origin));
    originsEl.replaceChildren(...(legend ? [legend] : []), ...rows);

    // One park checkbox per input column, pre-ticked for a required field or the key.
    renderParkFields();

    syncGo();
    syncDuplicate();
    renderPlainNotes();
    warningsEl.textContent = supportBlock(recordedWarnings(events));
    if (events.length === 0) notice(NO_EVENTS);
    else resultEl.textContent = "";
  }

  /** The origins still ticked, in the order the form rendered them. */
  function checkedOrigins(): string[] {
    const boxes = originsEl.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
    return Array.from(boxes)
      .filter((box) => box.checked)
      .map((box) => box.value);
  }

  /** The input columns still ticked to park, in the order the form rendered them. */
  function checkedParkFields(): string[] {
    const boxes = parkEl.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
    return Array.from(boxes)
      .filter((box) => box.checked)
      .map((box) => box.dataset.column ?? "")
      .filter((column) => column.length > 0);
  }

  async function submit(): Promise<void> {
    if (busy) return;
    if (events.length === 0) {
      notice(NO_EVENTS);
      return;
    }
    const name = nameEl.value.trim();
    if (name.length === 0) {
      // The same element carries this as before the restyle: a missing name is reported the way
      // every other refusal is, so which line a person reads never changed.
      showProblems(["Give the task a name first."]);
      return;
    }
    if (keyEl.value.length === 0) {
      notice("Choose which column identifies each row first.");
      return;
    }

    const request = makeRecipeRequest(events, {
      name,
      recordKeyColumn: keyEl.value,
      commitStepId: commitEl.value.length > 0 ? commitEl.value : null,
      // Unticking an origin the recording visited pins the shorter list, and the
      // route answers 422 naming that origin.
      allowedOrigins: checkedOrigins(),
      duplicateCheck: duplicateEl.checked,
      parkFields: checkedParkFields(),
    });

    busy = true;
    goEl.disabled = true;
    warningsEl.textContent = "";
    // Anything shown here while the request is in flight must not look like an outcome.
    notice(SAVING_NOTE);
    try {
      const outcome = await compileRecording(request, {
        fetchImpl: deps.fetchImpl,
        baseUrl: deps.baseUrl,
        accessCode: deps.accessCode,
      });
      if (!outcome.ok) {
        showProblems(outcome.problems);
        return;
      }
      // The compiler decides the shape; the rule's own sentence follows it (PL-077).
      applySearchKind(outcome.searchKind);
      // The run controls read these two keys, and the row is the sample row the route compiled.
      await deps.storage.set({ [RECIPE_KEY]: outcome.recipe, [ROW_KEY]: request.sample_row });
      // Compiling stored nothing: this POST is what makes the task real (PL-030). The two session
      // keys above are already written, so Try one row keeps working whatever happens here.
      const saved = await saveTask(
        { name, recipe: outcome.recipe },
        { fetchImpl: deps.fetchImpl, baseUrl: deps.baseUrl, accessCode: deps.accessCode },
      );
      if (!saved.ok) {
        notice(`${SAVE_TASK_REFUSAL}: ${plainReason(saved.problems)}`);
        return;
      }
      showSaved(name, saved.workflowId);
      // The recorder's caveats come first: they describe the recording itself, and the
      // route's warnings describe the task the compiler wrote from it. Both are for support
      // now; the person reads the save result and the two plain notes (PL-033 requirement 3).
      warningsEl.textContent = supportBlock([...recordedWarnings(events), ...outcome.warnings]);
    } catch (error) {
      showProblems([`ProcessLens could not be reached: ${describe(error)}.`]);
    } finally {
      busy = false;
      syncGo();
    }
  }

  return { refresh, submit };
}
