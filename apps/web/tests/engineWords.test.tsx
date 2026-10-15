/**
 * Requirement 7: no engine word ever reaches a person's eyes.
 *
 * Every page is rendered with sample data, the visible text of the rendered tree is collected, and
 * the test fails if it contains any of the forbidden words below -- case-insensitively, on the text
 * a person sees (never on an attribute value). Each forbidden word maps to the plain wording
 * DESIGN.md §2 requires instead:
 *
 *   recipe       -> "task"
 *   ledger       -> never shown (the ledger is not a thing a person has)
 *   parked       -> "needs your help"
 *   record key   -> "row", or the person's or thing's name ("Priya Nair")
 *   verified     -> "added", "updated" or "done" (the task's verb)
 *   rung         -> never shown (a step's target ladder is internal)
 *   idempotency  -> never shown
 *   v1, v2       -> never shown (a task's history says "Changed on 14 Oct")
 *   step_        -> never shown (a step is one sentence: "Click Save")
 *   fill-, click- -> never shown (the element's own words, not its code)
 *
 * The sample data is plain too: a fixture may not carry a forbidden word, or the test would only be
 * testing itself. The identifiers the API sends *as* engine words (`state: "parked"`, the keys of
 * `counts`) are excluded from that check on purpose -- every page maps them through `lib/words.ts`
 * before they can be rendered, which is exactly what the page checks below prove.
 */

import { describe, expect, it } from "vitest";

import type {
  HomeSummary,
  NeedsHelpRow,
  RecordRow,
  RunDetail as RunDetailPayload,
  RunSummary,
  TaskRow,
} from "../src/types";
import { createFetchStub, renderApp, settle, type StubRoutes } from "./helpers";

const OPERATOR_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";

interface Forbidden {
  engine: string;
  pattern: RegExp;
  plain: string;
}

const FORBIDDEN: Forbidden[] = [
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

/** Every forbidden word this text contains, so a failure names the rule it broke. */
function engineWordsIn(text: string): string[] {
  return FORBIDDEN.filter((rule) => rule.pattern.test(text)).map((rule) => rule.engine);
}

/* ------------------------------------------------------------------ */
/* Sample data, written in the words a person would use                */
/* ------------------------------------------------------------------ */

const TASK: TaskRow = {
  workflow_id: "task-1",
  recipe_version_id: "version-1",
  name: "Add inquiries",
  app: "crm.example.com",
  last_run: {
    run_id: "run-1",
    status: "completed",
    started_at: "2026-10-15T09:00:00Z",
    counts: { verified: 4, skipped: 1, parked: 1 },
  },
};

const RUN_SUMMARY: RunSummary = {
  id: "run-1",
  status: "completed",
  pause_reason: null,
  created_at: "2026-10-15T09:00:00Z",
  recipe_name: "Add inquiries",
  recipe_version: "version-1",
  counts: { verified: 4, skipped: 1, parked: 1 },
};

const RUN_DETAIL: RunDetailPayload = {
  id: "run-1",
  status: "completed",
  pause_reason: null,
  counts: { total: 6, verified: 4, skipped: 1, parked: 1, failed: 0 },
  open_approval: null,
  started_at: "2026-10-15T09:00:00Z",
  finished_at: "2026-10-15T09:15:00Z",
};

const RUN_ROWS: RecordRow[] = [
  {
    id: "row-a",
    record_key: "Priya Nair",
    state: "verified",
    reason: null,
    attempts: 1,
    max_attempts: 3,
    commit_attempted: true,
    destination_ref: "crm.example.com/leads/9",
  },
  {
    id: "row-b",
    record_key: "ACME-1",
    state: "skipped",
    reason: "duplicate",
    attempts: 1,
    max_attempts: 3,
    commit_attempted: false,
    destination_ref: null,
  },
  {
    id: "row-c",
    record_key: "ACME-2",
    state: "parked",
    reason: "missing email",
    attempts: 2,
    max_attempts: 3,
    commit_attempted: true,
    destination_ref: null,
  },
];

const HELP_ROWS: NeedsHelpRow[] = [
  {
    record_id: "row-c",
    run_id: "run-1",
    task_name: "Add inquiries",
    row: "ACME-2",
    state: "parked",
    reason: "missing email",
    commit_attempted: true,
  },
];

const HOME: HomeSummary = {
  window_days: 7,
  counts: { done: 4, already_there: 1, needs_help: 1 },
  minutes_saved: 34,
  needs_help: HELP_ROWS,
  running: [{ run_id: "run-2", task_name: "Add inquiries", done: 11, total: 20, already_there: 2 }],
  tasks: [TASK],
};

/** The stored task's document, as `GET /api/v1/recipes/{id}` answers it (builder C reads it). */
const RECIPE = {
  id: "version-1",
  workflow_id: "task-1",
  workflow_name: "Add inquiries",
  version: 2,
  name: "Add inquiries",
  recipe: {
    steps: [
      { action: "navigate", url: "https://crm.example.com/leads" },
      { action: "fill", targets: [{ by: "label", text: "Email" }], value: "{{row.email}}" },
      { action: "click", targets: [{ by: "role", name: "Save" }] },
      { action: "end_record" },
    ],
  },
};

function routes(extra: StubRoutes = {}): StubRoutes {
  return {
    "/api/v1/home": { body: HOME },
    "/api/v1/needs-help": { body: HELP_ROWS },
    "/api/v1/operator": { body: { operator_id: OPERATOR_ID } },
    "/api/v1/executor/session": { body: { attached: true } },
    "/api/v1/tasks": { body: [TASK] },
    "/api/v1/runs/run-1/records": { body: RUN_ROWS },
    "/api/v1/runs/run-1": { body: RUN_DETAIL },
    "/api/v1/runs": { body: [RUN_SUMMARY] },
    "/api/v1/recipes/version-1": { body: RECIPE },
    ...extra,
  };
}

/** The text a person sees: the rendered tree's text, never an attribute value. */
function visibleText(container: HTMLElement): string {
  return container.textContent ?? "";
}

async function textOf(path: string): Promise<string> {
  const stub = createFetchStub(routes());
  const view = renderApp(path);
  await settle();
  const text = visibleText(view.container);
  stub.restore();
  view.unmount();
  return text;
}

const PAGES: { name: string; path: string; shows: string[] }[] = [
  { name: "Home", path: "/", shows: ["rows done this week.", "Add inquiries"] },
  { name: "Needs help", path: "/needs-help", shows: ["ACME-2", "Missing email."] },
  { name: "Tasks", path: "/tasks", shows: ["Add inquiries", "crm.example.com"] },
  { name: "Task detail", path: "/tasks/task-1", shows: ["Add inquiries", "Type the email"] },
  { name: "Runs", path: "/runs", shows: ["Add inquiries"] },
  { name: "Run detail", path: "/runs/run-1", shows: ["Priya Nair", "ACME-2"] },
  { name: "ComingSoon", path: "/teach", shows: ["Teaching a new task"] },
];

/* ------------------------------------------------------------------ */

describe("the checker itself", () => {
  it("catches an engine word and leaves ordinary words alone", () => {
    expect(engineWordsIn("This recipe was verified and then parked")).toEqual([
      "recipe",
      "parked",
      "verified",
    ]);
    expect(engineWordsIn("The row key: Priya Nair")).toEqual([]);
    expect(engineWordsIn("row-rv-1 and row-v12 are ids, not words")).toEqual([]);
    expect(engineWordsIn("It was added twice")).toEqual([]);
  });
});

describe("every page shows only plain words", () => {
  for (const page of PAGES) {
    it(`${page.name} shows none of the forbidden words`, async () => {
      const text = await textOf(page.path);
      expect(text.trim()).not.toBe("");
      // The page really rendered its sample data, so a blank screen can never pass this. 
      for (const expected of page.shows) {
        expect(text, `${page.name} should show "${expected}"`).toContain(expected);
      }
      const found = engineWordsIn(text);
      // The message names each word and the DESIGN.md §2 wording it must appear as instead.
      const detail = found
        .map((engine) => `${engine} (show "${FORBIDDEN.find((r) => r.engine === engine)?.plain}")`)
        .join(", ");
      expect(found, `${page.name} must say: ${detail}`).toEqual([]);
    });
  }
});

describe("the sample data", () => {
  it("carries no forbidden word in anything a page shows", () => {
    // An engine state or a count name is an identifier the API sends, and every page maps it
    // through lib/words.ts; those keys are not shown, so they are skipped here.
    const notShown = new Set([
      "state",
      "status",
      "counts",
      "pause_reason",
      "id",
      "run_id",
      "record_id",
      "row_id",
      "workflow_id",
      "recipe_version_id",
      "recipe_version",
    ]);
    const shown: string[] = [];
    const walk = (value: unknown, key: string): void => {
      if (notShown.has(key)) return;
      if (typeof value === "string") {
        shown.push(value);
        return;
      }
      if (Array.isArray(value)) {
        for (const entry of value) walk(entry, key);
        return;
      }
      if (value !== null && typeof value === "object") {
        for (const [name, entry] of Object.entries(value as Record<string, unknown>)) {
          walk(entry, name);
        }
      }
    };
    walk({ HOME, TASK, RUN_SUMMARY, RUN_DETAIL, RUN_ROWS, HELP_ROWS, RECIPE }, "root");

    for (const value of shown) {
      expect(engineWordsIn(value), `the sample data says "${value}"`).toEqual([]);
    }
  });
});
