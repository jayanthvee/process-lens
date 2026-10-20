/**
 * PL-062: the edit model for a task's steps.
 *
 * A stored version is immutable once a run has pinned it, so an edit never changes one. These are
 * pure functions: each takes the loaded document and returns a new document, or one refusal
 * sentence for the page to show on the row. The page posts the result as the task's next version;
 * nothing here touches the network.
 *
 * The whole scope is rename, remove, where a value comes from, and how the save is checked. Element
 * targets are not edited here: a step's targets decide what it clicks, and changing those is
 * teaching that part of the task again.
 */

/** A recipe document, as `GET /api/v1/recipes/{id}` returns it. Only the fields read here. */
export interface RecipeDocument {
  steps?: unknown;
  meta?: unknown;
  [key: string]: unknown;
}

/** One step, guarded: the document comes from the API, so nothing is assumed to be there. */
export type Step = Record<string, unknown>;

/** The step fields that hold a nested step list (the schema's own names). */
const NESTED_KEYS = ["then", "else", "steps"] as const;

/** The name a step may carry: the schema's `step_name` limit. */
export const NAME_LIMIT = 120;

/** The wait a save check gets when the recorded one had none. */
export const DEFAULT_CHECK_TIMEOUT_MS = 8000;

export interface EditAccepted {
  ok: true;
  document: RecipeDocument;
}

export interface EditRefused {
  ok: false;
  refusal: string;
}

export type EditResult = EditAccepted | EditRefused;

function accept(document: RecipeDocument): EditAccepted {
  return { ok: true, document };
}

function refuse(refusal: string): EditRefused {
  return { ok: false, refusal };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function asStepList(value: unknown): Step[] | null {
  if (!Array.isArray(value)) return null;
  return value.every(isObject) ? (value as Step[]) : null;
}

function clone(document: RecipeDocument): RecipeDocument {
  return JSON.parse(JSON.stringify(document)) as RecipeDocument;
}

function topSteps(document: RecipeDocument): Step[] {
  return asStepList(document.steps) ?? [];
}

/** One rendered line: the step, and how deep inside a branch it sits. */
export interface FlatStep {
  step: Step;
  depth: number;
}

function flattenList(list: Step[], depth: number): FlatStep[] {
  const out: FlatStep[] = [];
  for (const step of list) {
    out.push({ step, depth });
    for (const key of NESTED_KEYS) {
      const inner = asStepList(step[key]);
      if (inner !== null) out.push(...flattenList(inner, depth + 1));
    }
  }
  return out;
}

/** Every step of the document, in the order the run follows them (a nested list follows its parent). */
export function flattenSteps(document: RecipeDocument): FlatStep[] {
  return flattenList(topSteps(document), 0);
}

function findStep(document: RecipeDocument, stepId: string): Step | undefined {
  return flattenSteps(document).find((entry) => entry.step.id === stepId)?.step;
}

interface Location {
  list: Step[];
  index: number;
  nested: boolean;
}

function locate(list: Step[], stepId: string, nested: boolean): Location | null {
  const index = list.findIndex((step) => step.id === stepId);
  if (index >= 0) return { list, index, nested };
  for (const step of list) {
    for (const key of NESTED_KEYS) {
      const inner = asStepList(step[key]);
      if (inner === null) continue;
      const found = locate(inner, stepId, true);
      if (found !== null) return found;
    }
  }
  return null;
}

/** The step's own name, when it has one; the recorder writes "fill Contact" style names. */
export function stepName(step: Step): string {
  return typeof step.name === "string" ? step.name : "";
}

/** The columns `meta.input_schema.fields` declares, in the order the document lists them. */
export function columnsOf(document: RecipeDocument): string[] {
  if (!isObject(document.meta)) return [];
  const schema = document.meta.input_schema;
  if (!isObject(schema)) return [];
  const fields = schema.fields;
  return isObject(fields) ? Object.keys(fields) : [];
}

/** Give one step a new name. */
export function renameStep(document: RecipeDocument, stepId: string, name: string): EditResult {
  const clean = name.trim();
  if (clean === "") return refuse("Give the step a name.");
  if (clean.length > NAME_LIMIT) {
    return refuse(`That name is too long. Use ${NAME_LIMIT} characters or fewer.`);
  }
  const next = clone(document);
  const step = findStep(next, stepId);
  if (step === undefined) return refuse("That step is no longer here.");
  step.name = clean;
  return accept(next);
}

/**
 * A step is named by another step when something points at it. The v1 schema has no such field --
 * a branch and a loop hold their inner steps inline -- but a `goto` is the name this format would
 * use, and a document that carries one must not lose the step it points at.
 */
function namesStep(document: RecipeDocument, stepId: string): boolean {
  return flattenSteps(document).some((entry) => entry.step.goto === stepId);
}

/** Remove one step, unless the document would lose something it needs. */
export function removeStep(document: RecipeDocument, stepId: string): EditResult {
  const next = clone(document);
  const step = findStep(next, stepId);
  if (step === undefined) return refuse("That step is no longer here.");
  if (step.commit === true) return refuse("This is the step that saves; it can't be removed.");
  if (namesStep(next, stepId)) {
    return refuse("Another step uses this one, so it can't be removed.");
  }
  const where = locate(topSteps(next), stepId, false);
  if (where === null) return refuse("That step is no longer here.");
  if (where.list.length === 1) {
    return refuse(
      where.nested
        ? "This branch needs at least one step, so this one can't be removed."
        : "This is the last step, so it can't be removed.",
    );
  }
  where.list.splice(where.index, 1);
  return accept(next);
}

/** Where a fill or a select gets its value: a spreadsheet column, or text it always uses. */
export type ValueChoice = { kind: "input"; column: string } | { kind: "constant"; value: string };

/** Point a fill or a select at a column or at fixed text. Never at AI text. */
export function setValueSource(
  document: RecipeDocument,
  stepId: string,
  source: ValueChoice,
): EditResult {
  const kind = (source as { kind?: unknown }).kind;
  if (kind !== "input" && kind !== "constant") return refuse("AI text can't be set here.");
  const next = clone(document);
  const step = findStep(next, stepId);
  if (step === undefined) return refuse("That step is no longer here.");
  const action = step.action;
  if (action !== "fill" && action !== "select") {
    return refuse("Only a step that types or chooses has a value to change.");
  }
  if (kind === "input") {
    const column = (source as { column: string }).column;
    if (!columnsOf(next).includes(column)) return refuse("That column isn't in the spreadsheet.");
    step.value = { kind: "input", column };
    return accept(next);
  }
  const value = String((source as { value: string }).value ?? "").trim();
  if (value === "") return refuse("Type what it should always say.");
  step.value = { kind: "constant", value };
  return accept(next);
}

/** How the page knows the save worked: the text it shows, the address it goes to, or unchanged. */
export type SaveCheckChoice =
  | { kind: "text_visible"; text: string }
  | { kind: "url_matches"; path: string }
  | { kind: "keep" };

/** A typed path as a pattern: every pattern character is escaped, and the address must end there. */
export function escapePattern(path: string): string {
  return `${path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`;
}

function checkTimeout(step: Step): number {
  const check = step.assert_after;
  if (isObject(check) && typeof check.timeout_ms === "number") return check.timeout_ms;
  return DEFAULT_CHECK_TIMEOUT_MS;
}

/** Set how a commit step proves it saved. The recorded wait is kept. */
export function setSaveCheck(
  document: RecipeDocument,
  stepId: string,
  check: SaveCheckChoice,
): EditResult {
  const next = clone(document);
  const step = findStep(next, stepId);
  if (step === undefined) return refuse("That step is no longer here.");
  if (step.commit !== true) return refuse("Only the step that saves has a save check.");
  if (check.kind === "keep") return accept(next);
  const timeout_ms = checkTimeout(step);
  if (check.kind === "text_visible") {
    const text = check.text.trim();
    if (text === "") return refuse("Type the text the page shows after saving.");
    step.assert_after = { text_visible: text, timeout_ms };
    return accept(next);
  }
  const path = check.path.trim();
  if (path === "") return refuse("Type the address the page goes to after saving.");
  step.assert_after = { url_matches: escapePattern(path), timeout_ms };
  return accept(next);
}

function valueClause(step: Step, place: number): string {
  const value = step.value;
  if (isObject(value) && value.kind === "input" && typeof value.column === "string") {
    return `step ${place} now comes from the "${value.column}" column`;
  }
  if (isObject(value) && value.kind === "constant" && typeof value.value === "string") {
    return `step ${place} always says "${value.value}"`;
  }
  return `step ${place} has a new value`;
}

function saveClause(step: Step): string {
  const check = step.assert_after;
  if (isObject(check) && "url_matches" in check) {
    return "the save is now checked by the address";
  }
  if (isObject(check) && "text_visible" in check) {
    return "the save is now checked by the text on the page";
  }
  return "the save has a new check";
}

/**
 * One plain sentence for the change reason the API stores beside the new version:
 * "Renamed step 2; the save is now checked by the address."
 */
export function diffSummary(before: RecipeDocument, after: RecipeDocument): string {
  const afterSteps = flattenSteps(after);
  const placeOf = new Map(afterSteps.map((entry, index) => [String(entry.step.id), index + 1]));
  const beforeById = new Map(
    flattenSteps(before).map((entry) => [String(entry.step.id), entry.step]),
  );

  const clauses: string[] = [];

  for (const entry of afterSteps) {
    const old = beforeById.get(String(entry.step.id));
    if (old === undefined) continue;
    if (stepName(old) !== stepName(entry.step)) {
      clauses.push(`renamed step ${placeOf.get(String(entry.step.id)) ?? 0}`);
    }
  }

  const afterIds = new Set(afterSteps.map((entry) => String(entry.step.id)));
  flattenSteps(before).forEach((entry, index) => {
    if (!afterIds.has(String(entry.step.id))) clauses.push(`removed step ${index + 1}`);
  });

  for (const entry of afterSteps) {
    const old = beforeById.get(String(entry.step.id));
    if (old === undefined) continue;
    const action = entry.step.action;
    if (action !== "fill" && action !== "select") continue;
    if (JSON.stringify(old.value) === JSON.stringify(entry.step.value)) continue;
    clauses.push(valueClause(entry.step, placeOf.get(String(entry.step.id)) ?? 0));
  }

  for (const entry of afterSteps) {
    const old = beforeById.get(String(entry.step.id));
    if (old === undefined || entry.step.commit !== true || old.commit !== true) continue;
    if (JSON.stringify(old.assert_after) === JSON.stringify(entry.step.assert_after)) continue;
    clauses.push(saveClause(entry.step));
  }

  if (clauses.length === 0) return "The steps are unchanged.";
  const sentence = clauses.join("; ");
  return `${sentence.charAt(0).toUpperCase()}${sentence.slice(1)}.`;
}

/**
 * The API's problems, as sentences a person can act on.
 *
 * A problem names a JSON path and an `assert_after`; those are for whoever wrote the document. The
 * page shows what the person can act on instead, once each.
 */
export function problemsInPlainWords(problems: string[]): string[] {
  const sentences: string[] = [];
  const add = (sentence: string): void => {
    if (!sentences.includes(sentence)) sentences.push(sentence);
  };
  for (const problem of problems) {
    const text = problem.toLowerCase();
    if (text.includes("assert_after") || text.includes("commit")) {
      add("The step that saves has to say how we know it saved.");
    } else if (text.includes("required")) {
      add("Something the steps need is missing.");
    } else {
      add("ProcessLens couldn't use these steps as they are.");
    }
  }
  if (sentences.length === 0) add("ProcessLens couldn't use these steps as they are.");
  return sentences;
}
