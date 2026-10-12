// The recorder against the messy fixture site's real markup (PL-016 requirement 11).
//
// `fixtures/sites/messy-crm` serves the shapes that broke the recorder on real pages: ids it
// mints fresh on every load, a removable recipient chip whose ✕ carries its own name, a
// contenteditable notes box, and a full-page save. The served pages are templates — the server
// substitutes a generated id for every `{{ID_*}}` token — so these tests do exactly what the
// server does, then drive the recorder over the result. The html is the fixture; this is the
// best available stand-in for a browser until PL-016b drives the real one.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { attachRecorder } from "../src/content/recorder";
import type { PendingEvent } from "../src/shared/events";
import { resetStepIds } from "../src/shared/events";
import type { LocatorRung } from "../src/shared/recipe-types";

const FIXTURE_DIR = resolve(process.cwd(), "../../fixtures/sites/messy-crm");

/**
 * One load of a fixture page: the server mints a fresh id for every token on every render.
 *
 * The ids are the three shapes the ticket names — Gmail's `:xx`, a long hex run, and React's
 * `:r1:` — so a recorder that trusted them records a selector that dies on the next load. The
 * stable handles (label, `aria-label`, `data-testid`) are in the file itself.
 */
function loadPage(name: string, mint: (token: string) => string = gmailId): Document {
  const raw = readFileSync(resolve(FIXTURE_DIR, "public", name), "utf8");
  const html = raw.replace(/\{\{([A-Z0-9_]+)\}\}/g, (_match, token: string) => {
    const id = mint(token);
    mintedIds.push(id);
    return id;
  });
  const doc = document.implementation.createHTMLDocument("messy-crm");
  doc.documentElement.innerHTML = html;
  document.body.innerHTML = doc.body.innerHTML;
  return document;
}

/** Every id the last `loadPage` minted, so a test can prove none of them was used as a rung. */
let mintedIds: string[] = [];

let minted = 0;
function gmailId(): string {
  minted += 1;
  return `:${minted.toString(36)}b`;
}

function hexId(): string {
  minted += 1;
  return `${(minted + 0x4a7f).toString(16)}${"0".repeat(8)}`.slice(0, 12);
}

function reactId(): string {
  minted += 1;
  return `:r${minted}:`;
}

interface Harness {
  events: PendingEvent[];
  detach: () => void;
}

function harness(): Harness {
  const events: PendingEvent[] = [];
  const detach = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => true,
    url: () => "http://127.0.0.1:8123/recipients.html",
    now: () => "2026-10-12T00:00:00.000Z",
  });
  return { events, detach };
}

function cssRungs(targets: LocatorRung[]): string[] {
  return targets
    .filter((rung): rung is Extract<LocatorRung, { by: "css" }> => rung.by === "css")
    .map((rung) => rung.selector);
}

beforeEach(() => {
  document.body.innerHTML = "";
  minted = 0;
  mintedIds = [];
  resetStepIds();
});

describe("the messy fixture's recipient chip", () => {
  it("records a click on the ✕ by the ✕'s own name, not the chip's", () => {
    loadPage("recipients.html");
    const recorded = harness();
    const remove = document.querySelector("button[aria-label='Remove alice@example.test']")!;

    remove.dispatchEvent(new Event("click", { bubbles: true }));

    expect(recorded.events).toHaveLength(1);
    const step = recorded.events[0]!.step!;
    expect(step.action).toBe("click");
    expect(step.targets[0]).toMatchObject({
      by: "role",
      role: "button",
      name: "Remove alice@example.test",
    });
    expect(JSON.stringify(step.targets)).not.toContain("recipient-chip");
  });

  it("records no generated id as a selector, with every id shape the fixture mints", () => {
    for (const mint of [gmailId, hexId, reactId]) {
      resetStepIds();
      minted = 0;
      mintedIds = [];
      document.body.innerHTML = "";
      loadPage("recipients.html", mint);
      const recorded = harness();
      const ids = [...mintedIds];

      document.querySelector("button[aria-label='Remove alice@example.test']")!
        .dispatchEvent(new Event("click", { bubbles: true }));

      const selectors = cssRungs(recorded.events[0]!.step!.targets).join(" ");
      // Not one id-shaped rung: no `#`, and none of the ids this load actually minted.
      expect(selectors).not.toContain("#");
      for (const id of ids) expect(selectors).not.toContain(id);
      expect(recorded.events[0]!.step!.targets.length).toBeGreaterThan(0);
    }
  });

  it("still records the chip itself when the chip is what was clicked", () => {
    loadPage("recipients.html");
    const recorded = harness();

    document.querySelector("[data-testid='recipient-chip']")!.dispatchEvent(
      new Event("click", { bubbles: true }),
    );

    expect(recorded.events).toHaveLength(1);
    const targets = recorded.events[0]!.step!.targets;
    // The chip, by its own name — never the ✕ nested inside it, and never the form around it.
    expect(targets[0]).toMatchObject({ by: "role", role: "option" });
    expect(JSON.stringify(targets)).not.toContain("Remove alice@example.test");
  });
});

describe("the messy fixture's notes box", () => {
  it("records typing into the contenteditable as one fill with its text", () => {
    loadPage("notes.html");
    const recorded = harness();
    const box = document.querySelector("[data-testid='notes-box']")!;

    box.textContent = "Called alice@example.test about the renewal.";
    box.dispatchEvent(new Event("input", { bubbles: true }));
    box.dispatchEvent(new Event("focusout", { bubbles: true }));

    expect(recorded.events).toHaveLength(1);
    expect(recorded.events[0]!.step).toMatchObject({
      action: "fill",
      value: { kind: "constant", value: "Called alice@example.test about the renewal." },
    });
  });
});

describe("the messy fixture's save", () => {
  it("records the Save button by its stable test id", () => {
    loadPage("notes.html");
    const recorded = harness();

    document.querySelector("[data-testid='save']")!.dispatchEvent(
      new Event("click", { bubbles: true }),
    );

    expect(recorded.events).toHaveLength(1);
    const targets = recorded.events[0]!.step!.targets;
    expect(targets.some((rung) => rung.by === "testid" && rung.value === "save")).toBe(true);
    expect(cssRungs(targets).join(" ")).not.toContain("#");
  });
});
