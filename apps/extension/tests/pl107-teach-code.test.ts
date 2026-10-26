// PL-107 item 12: while teaching, the code a site emails is looked up and typed for the person,
// exactly as a run types it. The typed code is never recorded.
//
// The page-side rules are general: any page `codePromptInPage` recognises, any submit control
// `enterCodeInPage` presses. The markup below is ordinary sign-in markup, not one site's.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { attachRecorder } from "../src/content/recorder";
import { teachCodeOnPage, type TeachCodePageDeps } from "../src/content/teach-code";
import { codePromptInPage, enterCodeInPage } from "../src/shared/code-page";
import type { PendingEvent } from "../src/shared/events";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

beforeEach(() => {
  document.body.innerHTML = "";
  document.title = "Sign in";
  restoreLayout = installLayout();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

const T0 = Date.parse("2026-10-26T10:00:00Z");
const SITE = "https://app.example.test/login";

function deps(overrides: Partial<TeachCodePageDeps> = {}): TeachCodePageDeps {
  return {
    probe: codePromptInPage,
    enter: enterCodeInPage,
    ask: async () => ({ status: "found", code: "482913" }),
    report: () => undefined,
    quietly: (work) => work(),
    now: () => T0,
    site: () => SITE,
    ...overrides,
  };
}

function codePage(): HTMLFormElement {
  document.body.innerHTML = `
    <h1>Check your email</h1>
    <form id="f">
      <input autocomplete="one-time-code" id="code" />
      <button type="submit" id="go">Verify</button>
    </form>`;
  return document.getElementById("f") as HTMLFormElement;
}

describe("a code page while teaching", () => {
  it("types the one code from the email and presses the page's own continue", async () => {
    const form = codePage();
    let submitted = 0;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submitted += 1;
    });
    const report = vi.fn();

    await teachCodeOnPage(deps({ report }), { attempted: false });

    expect((document.getElementById("code") as HTMLInputElement).value).toBe("482913");
    expect(submitted).toBeGreaterThan(0);
    // The answer never carries the code, and neither does what the panel is told.
    expect(report).toHaveBeenCalledWith({ entered: true, reason: "submitted" });
    expect(JSON.stringify(report.mock.calls)).not.toContain("482913");
  });

  it("records nothing the extension typed or pressed", async () => {
    codePage();
    const events: PendingEvent[] = [];
    // The same quiet window the content script uses: the recorder is off while the code is
    // entered, so neither the fill nor the press it makes becomes a step.
    let typing = false;
    const detach = attachRecorder(document, {
      send: (event) => events.push(event),
      isActive: () => !typing,
      url: () => SITE,
      now: () => "2026-10-26T10:00:00.000Z",
    });

    await teachCodeOnPage(
      deps({
        quietly: (work) => {
          typing = true;
          try {
            return work();
          } finally {
            typing = false;
          }
        },
      }),
      { attempted: false },
    );
    detach();

    expect(events).toEqual([]);
  });

  it("leaves a page that is not asking for a code alone", async () => {
    document.body.innerHTML = `<form><label>Email<input name="email" /></label></form>`;
    const ask = vi.fn(async () => ({ status: "found" as const, code: "482913" }));

    await teachCodeOnPage(deps({ ask }), { attempted: false });

    expect(ask).not.toHaveBeenCalled();
  });

  it("types nothing when the person has never said yes", async () => {
    codePage();
    const report = vi.fn();

    await teachCodeOnPage(
      deps({ ask: async () => ({ status: "not_allowed" }), report }),
      { attempted: false },
    );

    expect((document.getElementById("code") as HTMLInputElement).value).toBe("");
    expect(report).not.toHaveBeenCalled();
  });

  it("tries a code page once, not again for every action", async () => {
    codePage();
    const ask = vi.fn(async () => ({ status: "none" as const }));
    const state = { attempted: false };

    await teachCodeOnPage(deps({ ask }), state);
    await teachCodeOnPage(deps({ ask }), state);

    expect(ask).toHaveBeenCalledTimes(1);
  });

  it("says so when the code could not be typed, and never shows the code", async () => {
    codePage();
    const report = vi.fn();

    await teachCodeOnPage(
      deps({
        enter: () => ({ entered: false, reason: "not_found" }),
        report,
      }),
      { attempted: false },
    );

    expect(report).toHaveBeenCalledWith({ entered: false, reason: "not_found" });
    expect(JSON.stringify(report.mock.calls)).not.toContain("482913");
  });
});
