// PL-107: what the owner's first real-site teach (a sign-in with an emailed code) recorded wrong.
//
// Each case is a general rule; the markup is the shape a real page used, plus a second shape so a
// fix cannot just match one site.
import { beforeEach, describe, expect, it } from "vitest";

import { attachRecorder } from "../src/content/recorder";
import { stepLines } from "../src/popup/flow";
import { RecorderSession } from "../src/background/recorder-session";
import { isEmailHost } from "../src/shared/email-code";
import type { PendingEvent } from "../src/shared/events";
import { resetStepIds } from "../src/shared/events";
import { accessibleName, isPasswordField } from "../src/shared/fingerprint";
import type { RecordedEvent } from "../src/shared/recipe-types";

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
});

describe("a button's name leaves out what the page hides", () => {
  it("drops an aria-hidden loading label (Continue, not ContinueContinuing)", () => {
    document.body.innerHTML = `
      <button type="submit" form="login">
        <span><span>Continue</span><span aria-hidden="true" style="opacity:0">Continuing</span></span>
      </button>`;
    expect(accessibleName(document.querySelector("button")!)).toBe("Continue");
  });

  it("drops a hidden or display:none label too", () => {
    document.body.innerHTML = `
      <button><span>Save</span><span hidden>Saving…</span><span style="display:none">Saved</span></button>`;
    expect(accessibleName(document.querySelector("button")!)).toBe("Save");
  });
});

describe("every box of a split code is a code field", () => {
  it("no maxlength, labelled digit N of 6, only the first hinted one-time-code", () => {
    const boxes = Array.from({ length: 6 }, (_, i) => {
      const hint = i === 0 ? ' autocomplete="one-time-code"' : "";
      return `<input inputmode="numeric" aria-label="Digit ${i + 1} of 6"${hint}>`;
    }).join("");
    document.body.innerHTML = `<div><div>${boxes}</div></div>`;
    for (const box of Array.from(document.querySelectorAll("input"))) {
      expect(isPasswordField(box), box.getAttribute("aria-label") ?? "").toBe(true);
    }
  });

  it("a row of six plain text boxes where one is hinted counts as a code", () => {
    const boxes = Array.from({ length: 6 }, (_, i) =>
      i === 2 ? '<input type="text" name="otp-3">' : '<input type="text">',
    ).join("");
    document.body.innerHTML = `<form><div class="row">${boxes}</div></form>`;
    for (const box of Array.from(document.querySelectorAll("input"))) {
      expect(isPasswordField(box)).toBe(true);
    }
  });

  it("an ordinary form with six text fields is not a code", () => {
    document.body.innerHTML = `<form>${["First", "Last", "Email", "Phone", "City", "Zip"]
      .map((label) => `<label>${label}<input type="text"></label>`)
      .join("")}</form>`;
    for (const box of Array.from(document.querySelectorAll("input"))) {
      expect(isPasswordField(box)).toBe(false);
    }
  });

  it("typing a code into the boxes records no step at all", () => {
    const boxes = Array.from(
      { length: 6 },
      (_, i) => `<input inputmode="numeric" aria-label="Digit ${i + 1} of 6">`,
    ).join("");
    document.body.innerHTML = `<div>${boxes}</div>`;
    const events: PendingEvent[] = [];
    const detach = attachRecorder(document, {
      send: (event) => events.push(event),
      isActive: () => true,
      url: () => "https://app.example.test/login",
      now: () => "2026-10-26T10:00:00.000Z",
    });
    for (const [i, box] of Array.from(document.querySelectorAll("input")).entries()) {
      box.focus();
      box.value = String(i + 1);
      box.dispatchEvent(new Event("input", { bubbles: true }));
      box.dispatchEvent(new Event("change", { bubbles: true }));
      box.dispatchEvent(new Event("focusout", { bubbles: true }));
    }
    detach();
    const fills = events.filter((event) => event.step?.action === "fill");
    expect(fills).toEqual([]);
  });
});

describe("one click on a submit button is one line in the list", () => {
  const save = { by: "role", role: "button", name: "Continue" } as const;
  const events: RecordedEvent[] = [
    {
      seq: 1,
      at: "2026-10-26T10:00:00Z",
      kind: "click",
      url: "https://app.example.test/login",
      step: { id: "click-1", action: "click", targets: [save] },
    },
    {
      seq: 2,
      at: "2026-10-26T10:00:00Z",
      kind: "submit",
      url: "https://app.example.test/login",
      step: { id: "click-2", action: "click", targets: [save] },
    },
  ];

  it("folds the submit that a click caused into the click's line", () => {
    const lines = stepLines(events);
    expect(lines).toHaveLength(1);
    expect(lines[0]!.sentence).toBe("Clicked Continue");
  });
});

describe("teaching never records the person's email tab", () => {
  it("knows the common webmail hosts", () => {
    expect(isEmailHost("https://mail.google.com/mail/u/0/#inbox")).toBe(true);
    expect(isEmailHost("https://outlook.live.com/mail/0/")).toBe(true);
    expect(isEmailHost("https://outlook.office.com/mail/")).toBe(true);
    expect(isEmailHost("https://mail.yahoo.com/d/folders/1")).toBe(true);
    expect(isEmailHost("https://crm.example.com/leads")).toBe(false);
    expect(isEmailHost("https://www.google.com/search?q=mail")).toBe(false);
  });

  it("the session drops a step that happened in an email tab", () => {
    const session = new RecorderSession({ now: () => 0 });
    session.append({
      at: "2026-10-26T10:00:00Z",
      kind: "click",
      url: "https://mail.google.com/mail/u/0/#inbox",
      step: { id: "click-1", action: "click", targets: [{ by: "role", role: "link", name: "Inbox" }] },
    });
    session.append({
      at: "2026-10-26T10:00:01Z",
      kind: "click",
      url: "https://app.example.test/home",
      step: { id: "click-2", action: "click", targets: [{ by: "role", role: "button", name: "View results" }] },
    });
    expect(session.events.map((event) => event.url)).toEqual(["https://app.example.test/home"]);
  });
});
