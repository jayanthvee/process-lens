// "Save as task": the logic behind the popup form that turns the worker's
// recording into a compiled task, plus the small form object itself.
//
// The form is handed its root element, a storage area and a hub URL, so the
// compile request and the storage contract can be tested without a browser.
//
// Every sentence a person reads is spelled out here rather than built from an id:
// the engine's words (DESIGN.md §2) never reach the screen.
import type { RecordedEvent, RecordedStep } from "../shared/recipe-types";
import { UNNAMED_CLICK, UNNAMED_STEP } from "./words";

/** Where the compiled recipe and its sample row live for the Run button. */
export const RECIPE_KEY = "processlens:executor-recipe";
export const ROW_KEY = "processlens:executor-row";

const COMPILE_PATH = "/api/v1/recipes/compile";
const NO_EVENTS =
  "Nothing has been recorded yet. Stop teaching first, then save it as a task.";
/** Shown under the duplicate checkbox when the recording holds no search for the key. */
export const NO_SEARCH_HINT =
  "I did not see a search for this column, so I cannot check for duplicates.";

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
  | { ok: true; recipe: Record<string, unknown>; warnings: string[] }
  | { ok: false; problems: string[] };

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
 * Every distinct value the recording typed or chose, in recording order.
 *
 * A `fill`/`select` that carried a non-constant source is skipped: the compiler's
 * row can only hold what was actually demonstrated. A fill the envelope marks as
 * `supersedes`d contributes nothing either: it is the typed prefix an accepted
 * suggestion replaced, so the chip fill that absorbed it is the value and the
 * column the person wants to see -- the popup and the compiler must not disagree.
 */
export function recordedValues(events: RecordedEvent[]): RecordedValue[] {
  const values: RecordedValue[] = [];
  const seenValues = new Set<string>();
  const takenColumns = new Map<string, number>();

  // A fill another event's envelope names in `supersedes` is the typed prefix an accepted
  // suggestion replaced; the chip fill that absorbed it is the value and the column to offer.
  const superseded = new Set<string>();
  for (const event of events) {
    if (event?.supersedes) superseded.add(event.supersedes);
  }

  for (const event of events) {
    const step = event?.step ?? null;
    if (!step) continue;
    if (step.id !== undefined && superseded.has(String(step.id))) continue;
    const value = constantValue(step);
    if (value === null) continue;
    if (seenValues.has(value)) continue;
    seenValues.add(value);

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
 */
export function recordedWarnings(events: RecordedEvent[]): string[] {
  const warnings: string[] = [];
  for (const event of events) {
    const warning = event?.warning;
    if (!warning) continue;
    warnings.push(`${stepWords(event.step ?? null)}: ${warning}`);
  }
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
 * Whether the recording searches for the record key: a `fill` whose constant value equals
 * the record key column's sample value, immediately followed by a click that runs it -- a search
 * control, recognised by `looksLikeSearch` (mirrors the compiler's detection). Returns false when
 * no key column is chosen.
 */
export function searchDetected(events: RecordedEvent[], recordKeyColumn: string): boolean {
  const column = recordKeyColumn.trim();
  if (column.length === 0) return false;
  const sample = recordedValues(events).find((value) => value.column === column);
  if (!sample) return false;
  for (let index = 0; index < events.length - 1; index += 1) {
    const step = events[index]?.step ?? null;
    if (!step || step.action !== "fill") continue;
    if (constantValue(step) !== sample.value) continue;
    const next = events[index + 1]?.step ?? null;
    if (next && next.action === "click" && (looksLikeSearch(step) || looksLikeSearch(next))) {
      return true;
    }
  }
  return false;
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
  const sampleRow: Record<string, string> = {};
  for (const value of recordedValues(events)) sampleRow[value.column] = value.value;

  return {
    recording: events,
    name: choice.name,
    // This ticket's form has no separate "when to use" field, and the route needs
    // one, so the workflow name doubles as it until a later ticket adds the field.
    when_to_use: choice.name,
    sample_row: sampleRow,
    record_key_column: choice.recordKeyColumn,
    // The form pins the origins the person ticked; a direct caller that names none
    // gets the recording's own origins, exactly as before this field existed.
    allowed_origins: choice.allowedOrigins ?? recordedOrigins(events),
    commit_step_ids: choice.commitStepId ? [choice.commitStepId] : [],
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
  deps: { fetchImpl?: typeof fetch; baseUrl: string },
): Promise<CompileOutcome> {
  const fetchImpl = deps.fetchImpl ?? fetch;
  const baseUrl = deps.baseUrl.replace(/\/+$/, "");
  const response = await fetchImpl(`${baseUrl}${COMPILE_PATH}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
  });

  const body = await readJson(response);
  if (!response.ok) return { ok: false, problems: problemsFrom(response.status, body) };

  const payload = (body ?? {}) as { recipe?: unknown; warnings?: unknown };
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
  return { ok: true, recipe: recipe as Record<string, unknown>, warnings };
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
  onNotice?: (message: string) => void;
}

export interface MakeRecipeForm {
  refresh(events: RecordedEvent[]): void;
  submit(): Promise<void>;
}

function control<T extends Element>(scope: ParentNode, id: string): T {
  const found = scope.querySelector<T>(`#${id}`);
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
  const nameEl = control<HTMLInputElement>(scope, "make-name");
  const keyEl = control<HTMLSelectElement>(scope, "make-key");
  const commitEl = control<HTMLSelectElement>(scope, "make-commit");
  const duplicateEl = control<HTMLInputElement>(scope, "make-duplicate");
  const duplicateHintEl = control<HTMLParagraphElement>(scope, "make-duplicate-hint");
  const originsEl = control<HTMLFieldSetElement>(scope, "make-origins");
  const parkEl = control<HTMLFieldSetElement>(scope, "make-park-fields");
  const goEl = control<HTMLButtonElement>(scope, "make-go");
  const resultEl = control<HTMLParagraphElement>(scope, "make-result");
  const warningsEl = control<HTMLParagraphElement>(scope, "make-warnings");

  let events: RecordedEvent[] = [];
  let busy = false;

  function notice(message: string): void {
    resultEl.textContent = message;
    deps.onNotice?.(message);
  }

  /** The button follows the recording, the busy flag, and whether a key is chosen. */
  function syncGo(): void {
    goEl.disabled = events.length === 0 || busy || keyEl.value.length === 0;
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

  // A key the person picks re-evaluates the park ticks, the duplicate check and the button.
  keyEl.addEventListener("change", () => {
    syncGo();
    syncDuplicate();
    renderParkFields();
  });

  function showProblems(problems: string[]): void {
    // A rejection with no problems the route would own up to is still said in words.
    const sentences =
      problems.length > 0 ? problems : ["ProcessLens would not accept this recording."];
    const count = sentences.length === 1 ? "1 problem" : `${sentences.length} problems`;
    // The problems themselves go on their own line, one per line.
    notice(`Could not save this as a task (${count}).`);
    warningsEl.textContent = sentences.join("\n");
  }

  function refresh(next: RecordedEvent[]): void {
    events = Array.isArray(next) ? next : [];

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
    warningsEl.textContent = recordedWarnings(events).join("\n");
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
    notice("Turning your recording into a task…");
    try {
      const outcome = await compileRecording(request, {
        fetchImpl: deps.fetchImpl,
        baseUrl: deps.baseUrl,
      });
      if (!outcome.ok) {
        showProblems(outcome.problems);
        return;
      }
      // The run controls read these two keys, and the row is the sample row the route compiled.
      await deps.storage.set({ [RECIPE_KEY]: outcome.recipe, [ROW_KEY]: request.sample_row });
      notice(`Saved as a task: "${name}" is ready. Try one row will use it.`);
      // The recorder's caveats come first: they describe the recording itself, and the
      // route's warnings describe the task the compiler wrote from it.
      warningsEl.textContent = [...recordedWarnings(events), ...outcome.warnings].join("\n");
    } catch (error) {
      showProblems([`ProcessLens could not be reached: ${describe(error)}.`]);
    } finally {
      busy = false;
      syncGo();
    }
  }

  return { refresh, submit };
}
