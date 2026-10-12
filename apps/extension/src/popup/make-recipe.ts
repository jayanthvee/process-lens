// "Make recipe": the logic behind the popup form that turns the worker's
// recording into a compiled recipe, plus the small form object itself.
//
// The form is handed its root element, a storage area and a hub URL, so the
// compile request and the storage contract can be tested without a browser.
import type { RecordedEvent, RecordedStep } from "../shared/recipe-types";

/** Where the compiled recipe and its sample row live for the Run button. */
export const RECIPE_KEY = "processlens:executor-recipe";
export const ROW_KEY = "processlens:executor-row";

const COMPILE_PATH = "/api/v1/recipes/compile";
const NO_EVENTS = "No events recorded — stop a recording first.";

/** One recorded constant value, ready to become a row column. */
export interface RecordedValue {
  stepId: string;
  column: string;
  value: string;
  label: string;
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

/** Lowercase ASCII, runs of anything else collapsed to `-`, then trimmed. */
function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** Hands out `name`, then `name-2`, `name-3` as columns repeat. */
function allocate(base: string, taken: Map<string, number>): string {
  const first = base.length > 0 ? base : "value";
  const seen = taken.get(first) ?? 0;
  taken.set(first, seen + 1);
  return seen === 0 ? first : `${first}-${seen + 1}`;
}

/**
 * Every distinct value the recording typed or chose, in recording order.
 *
 * A `fill`/`select` that carried a non-constant source is skipped: the compiler's
 * row can only hold what was actually demonstrated.
 */
export function recordedValues(events: RecordedEvent[]): RecordedValue[] {
  const values: RecordedValue[] = [];
  const seenValues = new Set<string>();
  const takenColumns = new Map<string, number>();

  for (const event of events) {
    const step = event?.step ?? null;
    if (!step) continue;
    const value = constantValue(step);
    if (value === null) continue;
    if (seenValues.has(value)) continue;
    seenValues.add(value);

    const name = humanName(step);
    const column = allocate(slug(name ?? String(step.id ?? "")), takenColumns);
    values.push({ stepId: String(step.id ?? ""), column, value, label: name ?? column });
  }
  return values;
}

/**
 * The recorder's own caveats about the recording, one sentence per event that carries one.
 * The form shows these so a person sees a step that rests on a positional selector before
 * they compile, rather than after a replay moves the layout out from under it.
 */
export function recordedWarnings(events: RecordedEvent[]): string[] {
  const warnings: string[] = [];
  for (const event of events) {
    const warning = event?.warning;
    if (!warning) continue;
    warnings.push(`step ${event.step?.id ?? event.kind}: ${warning}`);
  }
  return warnings;
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

/** Every recorded step that can be the commit, labelled for the select. */
export function commitChoices(events: RecordedEvent[]): { id: string; label: string }[] {
  const choices: { id: string; label: string }[] = [];
  for (const event of events) {
    const step = event?.step ?? null;
    if (!step || step.action !== "click" || !step.id) continue;
    const name = step.name?.trim();
    choices.push({ id: step.id, label: name && name.length > 0 ? name : step.id });
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
  return [`The hub rejected the recording (HTTP ${status}).`];
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
    return { ok: false, problems: [`The hub answered HTTP ${response.status} without a recipe.`] };
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

/** A name to offer before the person types one of their own. */
function defaultName(events: RecordedEvent[]): string {
  try {
    const host = new URL(events[0]?.url ?? "").hostname;
    return host.length > 0 ? `Recorded on ${host}` : "Recorded workflow";
  } catch {
    return "Recorded workflow";
  }
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * The "Make recipe" form over the ids in popup.html: `make-name`, `make-key`,
 * `make-commit`, `make-origins`, `make-go`, `make-result` and `make-warnings`.
 *
 * `refresh` mirrors a recording into the controls and `submit` posts it; the
 * caller wires the button, so it can point `baseUrl` at the hub field first.
 */
export function createMakeRecipeForm(deps: MakeRecipeFormDeps): MakeRecipeForm {
  const scope = deps.root.querySelector<HTMLElement>("#make-recipe") ?? deps.root;
  const nameEl = control<HTMLInputElement>(scope, "make-name");
  const keyEl = control<HTMLSelectElement>(scope, "make-key");
  const commitEl = control<HTMLSelectElement>(scope, "make-commit");
  const originsEl = control<HTMLFieldSetElement>(scope, "make-origins");
  const goEl = control<HTMLButtonElement>(scope, "make-go");
  const resultEl = control<HTMLParagraphElement>(scope, "make-result");
  const warningsEl = control<HTMLParagraphElement>(scope, "make-warnings");

  let events: RecordedEvent[] = [];
  let busy = false;

  function notice(message: string): void {
    resultEl.textContent = message;
    deps.onNotice?.(message);
  }

  function showProblems(problems: string[]): void {
    const sentences = problems.length > 0 ? problems : ["The hub rejected the recording."];
    const count = sentences.length === 1 ? "1 problem" : `${sentences.length} problems`;
    // The problems themselves go on their own line, one per line.
    notice(`Could not make a recipe (${count}).`);
    warningsEl.textContent = sentences.join("\n");
  }

  function refresh(next: RecordedEvent[]): void {
    events = Array.isArray(next) ? next : [];

    const values = recordedValues(events);
    // A name the person typed survives a refresh; an empty box gets a default.
    if (nameEl.value.trim().length === 0) nameEl.value = defaultName(events);
    keyEl.replaceChildren(...values.map((value) => option(value.column, value.label)));
    keyEl.value = values[0]?.column ?? "";
    keyEl.disabled = values.length === 0;

    const choices = commitChoices(events);
    commitEl.replaceChildren(...choices.map((choice) => option(choice.id, choice.label)));
    commitEl.value = defaultCommitStepId(events) ?? "";
    commitEl.disabled = choices.length === 0;

    // Every origin starts ticked; the legend stays put when the recording has none.
    const legend = originsEl.querySelector("legend");
    const rows = recordedOrigins(events).map((origin) => originRow(origin));
    originsEl.replaceChildren(...(legend ? [legend] : []), ...rows);

    goEl.disabled = events.length === 0 || busy;
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

  async function submit(): Promise<void> {
    if (busy) return;
    if (events.length === 0) {
      notice(NO_EVENTS);
      return;
    }
    const name = nameEl.value.trim();
    if (name.length === 0) {
      showProblems(["Give the workflow a name before making the recipe."]);
      return;
    }

    const request = makeRecipeRequest(events, {
      name,
      recordKeyColumn: keyEl.value,
      commitStepId: commitEl.value.length > 0 ? commitEl.value : null,
      // Unticking an origin the recording visited pins the shorter list, and the
      // route answers 422 naming that origin.
      allowedOrigins: checkedOrigins(),
    });

    busy = true;
    goEl.disabled = true;
    warningsEl.textContent = "";
    notice("Compiling the recording…");
    try {
      const outcome = await compileRecording(request, {
        fetchImpl: deps.fetchImpl,
        baseUrl: deps.baseUrl,
      });
      if (!outcome.ok) {
        showProblems(outcome.problems);
        return;
      }
      // Run reads these two keys, and the row is the sample row the route compiled.
      await deps.storage.set({ [RECIPE_KEY]: outcome.recipe, [ROW_KEY]: request.sample_row });
      notice(`Recipe "${name}" is ready — Run will use it.`);
      // The recorder's caveats come first: they describe the recording itself, and the
      // route's warnings describe the recipe the compiler wrote from it.
      warningsEl.textContent = [...recordedWarnings(events), ...outcome.warnings].join("\n");
    } catch (error) {
      showProblems([`The hub could not be reached: ${describe(error)}.`]);
    } finally {
      busy = false;
      goEl.disabled = events.length === 0;
    }
  }

  return { refresh, submit };
}
