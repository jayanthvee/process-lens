// The popup in plain words (PL-025 requirement 2, 5).
//
// This file renders the popup's real markup, drives the real Save-as-task form with plain sample
// events, writes the step sentences into `#steps` exactly as the popup does, and then asserts that
// nothing a person can read carries an engine word. It also guards the two things the popup cannot
// get wrong without breaking the extension: it loads nothing remote, and every id the e2e harness
// clicks is still in the markup.
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it } from "vitest";
import { createMakeRecipeForm, REFUSAL_HEADLINE, SAVED_READY, SAVING_NOTE } from "../src/popup/make-recipe";
import {
  ENGINE_WORDS,
  engineWordsIn,
  plainDetail,
  recordingWords,
  runStateWords,
  sessionWords,
  STEPS_EMPTY,
  stepSentence,
  stepSentences,
  streamWords,
  UNNAMED_STEP,
} from "../src/popup/words";
import type { RecordedEvent } from "../src/shared/recipe-types";

const POPUP_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../src/popup");
const EXTENSION_DIR = resolve(POPUP_DIR, "../..");
const POPUP_HTML = readFileSync(resolve(POPUP_DIR, "popup.html"), "utf8");
const POPUP_CSS = readFileSync(resolve(POPUP_DIR, "popup.css"), "utf8");
const TOKENS_CSS = readFileSync(resolve(POPUP_DIR, "tokens.css"), "utf8");
const POPUP_BODY = /<body[^>]*>([\s\S]*?)<\/body>/.exec(POPUP_HTML)?.[1] ?? POPUP_HTML;

const STAMP = "2026-10-12T09:00:00.000Z";
const PAGE = "https://crm.example.test/leads";

/** Every id the e2e harness selects by, frozen by PL-025 requirement 4. */
const FROZEN_E2E_IDS = [
  "state",
  "start",
  "stop",
  "clear",
  "hub",
  "connect",
  "session-state",
  "make-recipe",
  "make-name",
  "make-key",
  "make-commit",
  "make-duplicate",
  "make-duplicate-hint",
  "make-origins",
  "make-park-fields",
  "make-go",
  "make-result",
  "make-warnings",
  "ws",
  "export",
  "note",
  "count",
  "stream",
  "run-start",
  "run-pause",
  "run-resume",
  "run-abort",
  "run-state",
  "steps",
];

/**
 * A lead recording, the shape the recorder produces. Its ids are `click-1` and friends, on purpose:
 * the popup must never show them, and the assertion below fails if it does. Every *visible* string
 * here is plain.
 */
function plainRecording(): RecordedEvent[] {
  return [
    {
      seq: 1,
      at: STAMP,
      kind: "click",
      url: PAGE,
      step: {
        id: "click-1",
        name: "New lead",
        action: "click",
        targets: [{ by: "role", role: "button", name: "New lead" }],
      },
    },
    {
      seq: 2,
      at: STAMP,
      kind: "fill",
      url: PAGE,
      step: {
        id: "fill-1",
        action: "fill",
        targets: [{ by: "role", role: "textbox", name: "Email" }],
        value: { kind: "constant", value: "ravi@example.test" },
      },
    },
    {
      seq: 3,
      at: STAMP,
      kind: "click",
      url: PAGE,
      step: {
        id: "click-2",
        name: "Save",
        action: "click",
        targets: [{ by: "label", text: "Save" }],
      },
    },
  ];
}

/** The visible strings of the sample data, so a passing test cannot be a vacuous one. */
function visibleSampleText(events: RecordedEvent[]): string {
  return events
    .map((event) => {
      const step = event.step;
      const ladder = (step?.targets ?? [])
        .map((target) => {
          if (target.by === "role") return target.name;
          if (target.by === "label") return target.text;
          if (target.by === "testid") return target.value;
          if (target.by === "css") return "";
          return target.value;
        })
        .join(" ");
      const value =
        step && (step.action === "fill" || step.action === "select") && step.value.kind === "constant"
          ? String(step.value.value)
          : "";
      return `${step?.name ?? ""} ${ladder} ${value} ${event.warning ?? ""}`;
    })
    .join(" ");
}

/** The popup's own live list: one plain sentence per step, or the one sentence of an empty list. */
function renderSteps(events: RecordedEvent[]): void {
  const list = document.getElementById("steps");
  if (!list) throw new Error("the popup markup is missing #steps");
  const sentences = stepSentences(events);
  if (sentences.length === 0) {
    const empty = document.createElement("li");
    empty.className = "steps__empty";
    empty.textContent = STEPS_EMPTY;
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(
    ...sentences.map((sentence) => {
      const item = document.createElement("li");
      item.className = "steps__item";
      item.textContent = sentence;
      return item;
    }),
  );
}

function mountForm(
  outcome: { ok?: boolean; status?: number; body?: unknown } = {},
): ReturnType<typeof createMakeRecipeForm> {
  const form = createMakeRecipeForm({
    root: document,
    storage: { get: async () => ({}), set: async () => undefined },
    fetchImpl: (async () => ({
      ok: outcome.ok ?? true,
      status: outcome.status ?? 200,
      json: async () => outcome.body ?? { recipe: { id: "new-lead" }, warnings: [] },
    })) as unknown as typeof fetch,
    baseUrl: "http://127.0.0.1:8000",
  });
  form.refresh(plainRecording());
  return form;
}

/** Every `url(...)` a stylesheet declares, in order. */
function cssUrls(css: string): string[] {
  return Array.from(css.matchAll(/url\(\s*['"]?([^'")]+)['"]?\s*\)/g)).map((match) => match[1] ?? "");
}

/** The `@font-face` blocks of a stylesheet, so a weight can be counted inside them. */
function fontFaces(css: string): string[] {
  return Array.from(css.matchAll(/@font-face\s*\{([\s\S]*?)\}/g)).map((match) => match[1] ?? "");
}

beforeEach(() => {
  document.body.innerHTML = POPUP_BODY;
});

describe("the popup's words", () => {
  it("shows no engine word anywhere a person can read", () => {
    mountForm();
    renderSteps(plainRecording());

    const visible = document.body.textContent ?? "";
    expect(engineWordsIn(visible)).toEqual([]);
    // The sentences are there, so the assertion above is not passing over an empty page.
    expect(visible).toContain("Clicked Save");
    expect(visible).toContain("Typed the email");
    expect(visible).toContain("Do the job once in your browser");
  });

  it("keeps what the compile route sends off the screen when it is written for an engineer", async () => {
    // These are the sentences the route really sends. A recognised assertion is reported as
    // `assertion for click-2 taken from the recorded observation`
    // (packages/compiler/compiler.py), and a refused navigation as
    // `... whose origin '...' is not in allowed_origins`. Both carry an engine word.
    const routeWarnings = [
      "assertion for click-2 taken from the recorded observation",
      "the check for when this row is already there was inferred",
    ];
    const form = mountForm({ body: { recipe: { id: "new-lead", steps: [] }, warnings: routeWarnings } });
    await form.submit();

    const result = document.getElementById("make-result")?.textContent ?? "";
    const warnings = document.getElementById("make-warnings")?.textContent ?? "";
    expect(result).toContain("is ready");
    expect(engineWordsIn(document.body.textContent ?? "")).toEqual([]);
    expect(warnings).not.toContain("click-2");
    // The readable one is still shown, and the one that was not is still accounted for.
    expect(warnings).toContain("inferred");
    expect(warnings).toContain("1 more note");

    // And the refusal path: the route's own problems, one of which names a step and a schema key.
    const refusal = mountForm({
      ok: false,
      status: 422,
      body: {
        detail: {
          message: "the recording cannot be compiled",
          problems: [
            "navigate step 7 goes to 'https://elsewhere.example/', whose origin is not in allowed_origins",
            "the commit click has no result to check",
          ],
        },
      },
    });
    await refusal.submit();

    const refused = document.getElementById("make-result")?.textContent ?? "";
    const shown = document.getElementById("make-warnings")?.textContent ?? "";
    // The count is the true one, even though one of the two cannot be shown.
    expect(refused).toContain("Could not save this as a task (2 problems).");
    expect(shown).toContain("the commit click has no result to check");
    expect(shown).toContain("1 more note");
    expect(engineWordsIn(document.body.textContent ?? "")).toEqual([]);
  });

  it("has plain sample data, and the guard still catches an engine word", () => {
    const sample = visibleSampleText(plainRecording());
    expect(sample).toContain("Save");
    expect(engineWordsIn(sample)).toEqual([]);
    // The guard itself works, so the empty list above means something.
    expect(engineWordsIn("the recipe is parked in the ledger")).toEqual([
      "recipe",
      "ledger",
      "parked",
    ]);
    // Requirement 7's twelve, plus the phrases DESIGN.md §2 names and the worker really sends.
    expect(ENGINE_WORDS.length).toBe(14);
    for (const engine of [
      "recipe",
      "ledger",
      "parked",
      "record key",
      "verified",
      "rung",
      "idempotency",
      "v1",
      "v2",
      "step_",
      "fill-",
      "click-",
    ]) {
      expect(ENGINE_WORDS.map((rule) => rule.engine), `${engine} must be enforced`).toContain(
        engine,
      );
    }
  });

  it("names a step by its own words and never by its id", () => {
    const events = plainRecording();
    expect(stepSentences(events)).toEqual([
      "Clicked New lead",
      "Typed the email",
      "Clicked Save",
    ]);

    const unnamed: RecordedEvent[] = [
      {
        seq: 1,
        at: STAMP,
        kind: "click",
        url: PAGE,
        step: { id: "click-9", action: "click", targets: [{ by: "css", selector: "button" }] },
      },
      { seq: 2, at: STAMP, kind: "unrecordable", url: PAGE, step: null, warning: "no button" },
    ];
    expect(stepSentences(unnamed)).toEqual(["Clicked a control on the page"]);
    expect(stepSentences(unnamed).join(" ")).not.toContain("click-9");
    expect(stepSentence(null)).toBe(UNNAMED_STEP);
    expect(stepSentence(null, "navigate")).toBe("Went to another page");
    for (const sentence of stepSentences(events)) expect(engineWordsIn(sentence)).toEqual([]);
  });

  it("keeps the step id as the commit option's value and never as its label", () => {
    mountForm();
    const commit = document.getElementById("make-commit") as HTMLSelectElement;
    expect(Array.from(commit.options).map((option) => option.value)).toEqual(["click-1", "click-2"]);
    expect(Array.from(commit.options).map((option) => option.textContent)).toEqual([
      "New lead",
      "Save",
    ]);
  });

  it("writes one plain sentence per step into #steps, and one when there is nothing recorded", () => {
    renderSteps([]);
    const empty = document.querySelector("#steps .steps__empty");
    expect(empty?.textContent).toBe(STEPS_EMPTY);
    expect(POPUP_BODY).toContain(STEPS_EMPTY);

    renderSteps(plainRecording());
    const items = Array.from(document.querySelectorAll("#steps .steps__item"));
    expect(items.map((item) => item.textContent)).toEqual([
      "Clicked New lead",
      "Typed the email",
      "Clicked Save",
    ]);
    expect(document.querySelector("#steps .steps__empty")).toBeNull();
  });

  it("says every state in plain words", () => {
    expect(recordingWords("idle")).toBe("Not recording");
    expect(recordingWords("recording")).toBe("Recording");
    expect(
      (["idle", "running", "paused", "aborted", "done", "failed"] as const).map(runStateWords),
    ).toEqual(["Nothing is running", "Running", "Paused", "Stopped", "Finished", "Didn't work"]);
    expect(sessionWords("ready")).toBe("Connected to ProcessLens");
    expect(sessionWords("idle")).toBe("Not connected");
    expect(sessionWords("disconnected")).toBe("Not connected");
    expect(sessionWords("ready", "bound to a tab")).toBe("Connected to ProcessLens — bound to a tab");
    expect(streamWords("", false)).toBe("Off");
    expect(streamWords("ws://127.0.0.1:8000/record", false)).toBe("Ready to send");
    expect(streamWords("ws://127.0.0.1:8000/record", true)).toBe("Sending");
    for (const words of [
      recordingWords("idle"),
      recordingWords("recording"),
      sessionWords("ready", "bound to a tab"),
      streamWords("ws://127.0.0.1:8000/record", true),
      UNNAMED_STEP,
    ]) {
      expect(engineWordsIn(words)).toEqual([]);
    }
  });

  it("keeps the worker's own detail off the screen when it is written for an engineer", () => {
    // These are the strings the service worker really sends as a session detail: a guard's refusal
    // and the step-runner's, verbatim (`src/executor/guards.ts`).
    const guards = [
      "the page origin https://elsewhere.example.test is not in allowed_origins",
      'the control "Delete lead" looks destructive ("delete"); set allow_destructive_control on the step to permit it',
    ];
    for (const detail of guards) {
      expect(engineWordsIn(detail), `${detail} must be caught`).not.toEqual([]);
      expect(plainDetail(detail)).toBe("");
      expect(engineWordsIn(sessionWords("disconnected", detail))).toEqual([]);
      // The state still says what happened, in plain words.
      expect(sessionWords("disconnected", detail)).toBe("Not connected");
    }
    // A detail a person can read is still shown.
    expect(plainDetail("bound to a tab")).toBe("bound to a tab");
    expect(plainDetail(undefined)).toBe("");
    expect(plainDetail("   ")).toBe("");
  });
});

describe("the popup loads nothing remote", () => {
  /*
   * What must not be remote is a *loaded resource*: the extension's CSP allows no remote font,
   * script or stylesheet. A form field's own default value is not a loaded resource, and `#hub`'s
   * visible default belongs in the markup where it has always been - taking it out to satisfy this
   * guard would be a behaviour change, which this ticket forbids. So the guard is about `src`,
   * `href` and CSS `url(...)`, and it says exactly that.
   */
  it("names no remote resource in the markup or in either stylesheet", () => {
    const loaded = [
      ...Array.from(POPUP_HTML.matchAll(/(?:src|href)="([^"]+)"/g), (m) => m[1] ?? ""),
      ...cssUrls(POPUP_CSS),
      ...cssUrls(TOKENS_CSS),
    ];
    expect(loaded.length).toBeGreaterThan(0);
    for (const target of loaded) {
      expect(target, `the popup must not load ${target} over the network`).not.toMatch(
        /^(?:https?:)?\/\//,
      );
    }
    // And nothing in the stylesheets reaches out either, however it is spelled.
    expect(POPUP_CSS).not.toMatch(/https?:\/\//);
    expect(TOKENS_CSS).not.toMatch(/https?:\/\//);
  });

  it("links the tokens before the popup's own stylesheet", () => {
    const tokens = POPUP_HTML.indexOf('href="tokens.css"');
    const popup = POPUP_HTML.indexOf('href="popup.css"');
    expect(tokens).toBeGreaterThan(-1);
    expect(popup).toBeGreaterThan(-1);
    expect(tokens).toBeLessThan(popup);
    // The harness loads the popup's script by exactly this tag.
    expect(POPUP_HTML).toContain('<script type="module" src="popup.js">');
  });
});

describe("the bundled font", () => {
  it("declares each weight once and every url resolves to a real file", () => {
    const weights = fontFaces(POPUP_CSS)
      .map((block) => /font-weight:\s*(\d+)/.exec(block)?.[1] ?? "")
      .filter((weight) => weight.length > 0);
    expect(weights.slice().sort()).toEqual(["300", "400", "500", "600"]);
    expect(fontFaces(POPUP_CSS).every((block) => /font-family:\s*"Public Sans"/.test(block))).toBe(
      true,
    );

    const urls = [...cssUrls(POPUP_CSS), ...cssUrls(TOKENS_CSS)];
    expect(urls.length).toBeGreaterThan(0);
    for (const raw of urls) {
      const reference = raw.trim();
      if (reference.startsWith("data:")) continue;
      // A font is bundled under `fonts/`, which builder 1 copies from @fontsource into dist/popup.
      const file = reference.startsWith("./fonts/")
        ? resolve(
            EXTENSION_DIR,
            "node_modules/@fontsource/public-sans/files",
            reference.slice("./fonts/".length),
          )
        : resolve(POPUP_DIR, reference);
      expect(existsSync(file), `${reference} is missing (looked in ${file})`).toBe(true);
      expect(statSync(file).size).toBeGreaterThan(0);
    }
  });
});

describe("the frozen e2e contract", () => {
  it("still has every id the harness selects by, exactly once", () => {
    // Parsed, not searched: a duplicated id would still `toContain` the string while
    // `getElementById` silently returned the first one, which is how the harness selects.
    const document_ = new DOMParser().parseFromString(POPUP_HTML, "text/html");
    const ids = Array.from(document_.querySelectorAll("[id]"), (node) => node.id);
    expect(ids.length).toBeGreaterThan(0);
    for (const id of FROZEN_E2E_IDS) {
      const matches = ids.filter((candidate) => candidate === id);
      expect(matches, `#${id} must be in popup.html exactly once`).toHaveLength(1);
    }
    // And a control survives only if it is still inside the panel the harness reads.
    const body = new DOMParser().parseFromString(POPUP_HTML, "text/html").body;
    for (const id of ["make-name", "make-key", "make-park-fields", "make-go", "make-result"]) {
      const node = body.querySelector(`#${id}`);
      expect(node, `#${id} must be a real element`).not.toBeNull();
      expect(body.querySelector("#make-recipe")?.contains(node ?? null), `#${id} is inside the form`).toBe(
        true,
      );
    }
  });

  it("keeps the elements the harness reads apart, with the kinds it needs", () => {
    const body = new DOMParser().parseFromString(POPUP_HTML, "text/html").body;
    // `fill` needs a real input; `select_option` needs a real select; `click` needs a button.
    expect(body.querySelector("#make-name")?.tagName).toBe("INPUT");
    expect(body.querySelector("#make-key")?.tagName).toBe("SELECT");
    expect(body.querySelector("#make-go")?.tagName).toBe("BUTTON");
    for (const id of ["start", "stop", "clear", "connect"]) {
      expect(body.querySelector(`#${id}`)?.tagName, `#${id} is a button`).toBe("BUTTON");
    }
    expect(body.querySelector("#hub")?.tagName).toBe("INPUT");
    // The two notes are separate elements: the harness reads each one's own text.
    expect(body.querySelector("#make-result")?.isSameNode(body.querySelector("#make-warnings"))).toBe(
      false,
    );
  });

  it("carries the two strings the harness's wait watches for", () => {
    // A success is "is ready", a failure "Could not" — and nothing in flight may look like either.
    expect(SAVED_READY).toBe("is ready");
    expect(REFUSAL_HEADLINE).toBe("Could not");
    expect(SAVING_NOTE).not.toContain(SAVED_READY);
    expect(SAVING_NOTE).not.toContain(REFUSAL_HEADLINE);
    // And the two sentences really carry them.
    expect(`Saved as a task: "New lead" ${SAVED_READY}. Try one row will use it.`).toContain(
      SAVED_READY,
    );
    expect(`${REFUSAL_HEADLINE} save this as a task (1 problem).`).toContain(REFUSAL_HEADLINE);
  });
});
