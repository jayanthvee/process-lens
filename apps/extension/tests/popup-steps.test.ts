// The popup in three steps (PL-033 requirement 1-4).
//
// The popup walks a person through Teach, Save and Open in ProcessLens, one card per step, with
// only the current step's button in gold. Everything else lives inside a closed `More` details,
// the saved task opens a real tab (`chrome.tabs.create`), and the popup connects by itself when
// it opens. The frozen e2e ids keep their ids and their behaviour (PL-025).
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it } from "vitest";
import {
  applyStep,
  autoConnect,
  currentStep,
  openTaskTab,
  taskUrl,
  type PopupStep,
} from "../src/popup/flow";
import { createMakeRecipeForm } from "../src/popup/make-recipe";
import {
  DUPLICATES_BLOCKED,
  ENGINE_WORDS,
  engineWordsIn,
  plainSaveNotes,
  sessionWords,
  stepCountWords,
} from "../src/popup/words";
import type { RecordedEvent } from "../src/shared/recipe-types";

const POPUP_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../src/popup");
const POPUP_HTML = readFileSync(resolve(POPUP_DIR, "popup.html"), "utf8");
const POPUP_BODY = /<body[^>]*>([\s\S]*?)<\/body>/.exec(POPUP_HTML)?.[1] ?? POPUP_HTML;

const STAMP = "2026-10-12T09:00:00.000Z";
const PAGE = "https://crm.example.test/leads";

/** The PL-031 compiler warnings, in the voice the compiler really sends. */
const PL031_WARNINGS = [
  "skipped the fill for f_email because the page set the value itself",
  "bound a literal into step fill-1",
  "templated the fill for the search box",
  "derived the input schema from the recording",
  "dropped container click click-1",
  "folded the retried fill into one step",
  "assertion for click-2 taken from the recorded observation",
  "this save is checked against the duplicate search",
];

/** A recording with a required fill and no search in it, so duplicates cannot be checked. */
function leadRecording(): RecordedEvent[] {
  return [
    {
      seq: 1,
      at: STAMP,
      kind: "fill",
      url: PAGE,
      required: true,
      step: {
        id: "fill-1",
        action: "fill",
        targets: [{ by: "role", role: "textbox", name: "Email" }],
        value: { kind: "constant", value: "ravi@example.test" },
      },
    },
    {
      seq: 2,
      at: STAMP,
      kind: "click",
      url: PAGE,
      step: {
        id: "click-1",
        name: "Save",
        action: "click",
        targets: [{ by: "label", text: "Save" }],
      },
    },
  ];
}

function mountForm(
  outcome: { body?: unknown } = {},
  saved?: { urls: string[] },
): ReturnType<typeof createMakeRecipeForm> {
  const form = createMakeRecipeForm({
    root: document,
    storage: { get: async () => ({}), set: async () => undefined },
    fetchImpl: (async (input: RequestInfo | URL) => {
      const saving = String(input).endsWith("/api/v1/tasks");
      return {
        ok: true,
        status: 200,
        json: async () =>
          saving
            ? { workflow_id: "wf-1" }
            : (outcome.body ?? { recipe: { id: "new-lead" }, warnings: [] }),
      };
    }) as unknown as typeof fetch,
    baseUrl: "http://127.0.0.1:8000",
    onSaved: (url) => saved?.urls.push(url),
  });
  return form;
}

beforeEach(() => {
  document.body.innerHTML = POPUP_BODY;
});

describe("the three steps", () => {
  it("walks Teach, Save and Open in ProcessLens, in order", () => {
    const cards = Array.from(document.querySelectorAll<HTMLElement>(".step-card"));
    expect(cards.map((card) => card.dataset.step)).toEqual(["teach", "save", "open"]);
    // The Teach card is the one a person starts on.
    expect(cards[0]!.classList.contains("is-current")).toBe(true);
    // The Open card is not on the screen until a task has been saved.
    expect(cards[2]!.hidden).toBe(true);
  });

  it("shows the gold button on the current step's card only", () => {
    for (const step of ["teach", "save", "open"] as PopupStep[]) {
      applyStep(document, step);
      const gold = Array.from(document.querySelectorAll<HTMLButtonElement>(".button--primary"));
      expect(gold, `${step} must have exactly one gold button`).toHaveLength(1);
      expect(gold[0]!.closest(".step-card")?.getAttribute("data-step")).toBe(step);
    }
  });

  it("keeps every control the flow needs in its own card", () => {
    const teach = document.querySelector('.step-card[data-step="teach"]');
    for (const id of ["start", "stop", "clear", "count", "steps", "state", "session-state"]) {
      expect(teach?.querySelector(`#${id}`), `#${id} belongs to Teach`).not.toBeNull();
    }
    const save = document.querySelector(".step-card[data-step='save'] #make-recipe");
    for (const id of [
      "make-name",
      "make-key",
      "make-duplicate",
      "make-park-fields",
      "make-go",
      "make-result",
      "make-plain",
    ]) {
      expect(save?.querySelector(`#${id}`), `#${id} belongs to Save`).not.toBeNull();
    }
    expect(document.querySelector("#open-task")?.tagName).toBe("BUTTON");
  });

  it("decides the current step from what the person has done", () => {
    expect(currentStep({ recording: false, hasEvents: false, savedTaskUrl: null })).toBe("teach");
    expect(currentStep({ recording: true, hasEvents: false, savedTaskUrl: null })).toBe("teach");
    expect(currentStep({ recording: false, hasEvents: true, savedTaskUrl: null })).toBe("save");
    expect(currentStep({ recording: false, hasEvents: true, savedTaskUrl: "u" })).toBe("open");
    // While the person is teaching, a saved task does not pull them away.
    expect(currentStep({ recording: true, hasEvents: true, savedTaskUrl: "u" })).toBe("teach");
  });
});

describe("More", () => {
  it("is closed by default and holds the controls most people never need", () => {
    const more = document.getElementById("more");
    expect(more?.tagName).toBe("DETAILS");
    expect((more as HTMLDetailsElement | null)?.open).toBe(false);
    expect(more?.querySelector("summary")?.textContent).toContain("More");
    for (const id of [
      // "Which click finishes the row?" — the last click is preselected, so it lives here.
      "make-commit",
      // Save a copy of the steps, the streaming field.
      "export",
      "ws",
      "stream",
      // The ProcessLens address, Connect and Disconnect.
      "hub",
      "connect",
      "disconnect",
      // The popup's own note, and the support notes.
      "note",
      "make-warnings",
    ]) {
      const node = document.getElementById(id);
      expect(node, `#${id} must sit under More`).not.toBeNull();
      expect(more?.contains(node), `#${id} must sit under More`).toBe(true);
    }
  });

  it("has no run buttons of its own: the dashboard's Try one row is the only one", () => {
    // PL-043: the old Try one row / Pause / Resume / Stop controls ran a task from inside the
    // popup, which the dashboard's "3. Try one row" replaced. None of them is left.
    for (const id of ["run-start", "run-pause", "run-resume", "run-abort", "run-state"]) {
      expect(document.getElementById(id), `#${id} must be gone`).toBeNull();
      expect(POPUP_BODY, `#${id} must not survive in the markup`).not.toContain(`id="${id}"`);
    }
    // And the copy that explained them goes with them.
    expect(POPUP_BODY).not.toMatch(/Try one row/);
    // The commit select stays: the Save form still uses it.
    expect(document.getElementById("make-commit"), "#make-commit is the form's").not.toBeNull();
  });

  it("keeps the support notes reachable but shut", () => {
    const support = document.getElementById("support-notes");
    expect(support?.tagName).toBe("DETAILS");
    expect((support as HTMLDetailsElement | null)?.open).toBe(false);
    expect(support?.querySelector("summary")?.textContent).toContain("Notes for support");
    expect(support?.contains(document.getElementById("make-warnings") ?? null)).toBe(true);
  });
});

describe("Open in ProcessLens", () => {
  it("opens a real tab, because a plain link does nothing in a popup", () => {
    const created: { url: string }[] = [];
    openTaskTab(
      { create: (options: { url: string }) => void created.push(options) },
      taskUrl("http://127.0.0.1:8000/", "wf-1"),
    );
    expect(created).toEqual([{ url: "http://127.0.0.1:8000/tasks/wf-1" }]);
  });

  it("carries the saved task's address to the button, not a link", () => {
    // An `<a href>` in an extension popup does nothing, so the markup has no links at all.
    expect(POPUP_BODY).not.toMatch(/<a[\s>]/);
    const saved: { urls: string[] } = { urls: [] };
    const form = mountForm({ body: { recipe: { id: "new-lead" }, warnings: [] } }, saved);
    form.refresh(leadRecording());
    return form.submit().then(() => {
      expect(saved.urls).toEqual(["http://127.0.0.1:8000/tasks/wf-1"]);
    });
  });
});

describe("plain words under the save result", () => {
  it("says the two sentences that matter, and nothing else", () => {
    expect(
      plainSaveNotes({ duplicatesBlocked: true, parkLabels: ["Email"] }),
    ).toEqual([DUPLICATES_BLOCKED, "Stops and asks when Email is empty."]);
    expect(plainSaveNotes({ duplicatesBlocked: true, parkLabels: [] })).toEqual([
      DUPLICATES_BLOCKED,
    ]);
    expect(plainSaveNotes({ duplicatesBlocked: false, parkLabels: ["Email", "Phone"] })).toEqual([
      "Stops and asks when Email or Phone is empty.",
    ]);
    expect(plainSaveNotes({ duplicatesBlocked: false, parkLabels: [] })).toEqual([]);
    expect(DUPLICATES_BLOCKED).toBe(
      "I couldn't check for duplicates because you didn't search first.",
    );
  });

  it("shows at most 2 sentences for the PL-031 warning list, and hides the rest under support", async () => {
    const form = mountForm({
      body: { recipe: { id: "new-lead" }, warnings: PL031_WARNINGS },
    });
    form.refresh(leadRecording());
    await form.submit();

    const plain = (document.getElementById("make-plain")?.textContent ?? "").trim();
    const sentences = plain.split(/(?<=\.)\s+/).filter((sentence) => sentence.length > 0);
    expect(sentences.length, "at most 2 sentences under the save result").toBeLessThanOrEqual(2);
    expect(engineWordsIn(plain)).toEqual([]);
    expect(plain).toContain("I couldn't check for duplicates because you didn't search first.");

    // Everything the compiler sent is still there, for support, under More.
    const support = document.getElementById("make-warnings")?.textContent ?? "";
    for (const warning of PL031_WARNINGS) expect(support).toContain(warning);
  });
});

describe("connecting is automatic and visible", () => {
  it("connects once by itself when the popup opens unattached", async () => {
    let asked = 0;
    let connects = 0;
    let hubUrl = "";
    const connected = await autoConnect({
      sessionState: async () => {
        asked += 1;
        return "idle";
      },
      connect: async (url) => {
        connects += 1;
        hubUrl = url;
      },
      hubUrl: () => "http://127.0.0.1:8000",
    });
    expect(connected).toBe(true);
    expect(asked).toBe(1);
    expect(connects).toBe(1);
    expect(hubUrl).toBe("http://127.0.0.1:8000");
  });

  it("does not reconnect when the session is already there", async () => {
    let connects = 0;
    const connected = await autoConnect({
      sessionState: async () => "ready",
      connect: async () => {
        connects += 1;
      },
      hubUrl: () => "http://127.0.0.1:8000",
    });
    expect(connected).toBe(false);
    expect(connects).toBe(0);
  });

  it("says what happened to the connection in plain words", () => {
    expect(sessionWords("ready")).toBe("Connected to ProcessLens");
    expect(sessionWords("idle")).toBe("ProcessLens isn't running");
    expect(sessionWords("disconnected")).toBe("ProcessLens isn't running");
  });
});

describe("the step count", () => {
  it("reads like a person counts", () => {
    expect(stepCountWords(0)).toBe("0 steps recorded");
    expect(stepCountWords(1)).toBe("1 step recorded");
    expect(stepCountWords(15)).toBe("15 steps recorded");
  });
});

describe("the PL-031 word list", () => {
  it("catches every compiler warning the engine sends", () => {
    for (const engine of [
      "skipped the fill",
      "bound a literal",
      "templated",
      "derived the input schema",
      "dropped container click",
      "folded the retried fill",
      "assertion for",
      "this save is checked",
    ]) {
      expect(ENGINE_WORDS.map((rule) => rule.engine), `${engine} must be enforced`).toContain(
        engine,
      );
    }
    for (const warning of PL031_WARNINGS) {
      expect(engineWordsIn(warning), `"${warning}" must be caught`).not.toEqual([]);
    }
  });
});
