// PL-068: a verification-code page is recognised, and a code supplied by a person is typed into
// it. Neither function ever returns, stores or logs the code.
//
// These are the page-side functions the executor evaluates in the tab, so they are exercised here
// directly against a real DOM (happy-dom), exactly as the other guards' page functions are.

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { codePromptInPage, enterCodeInPage } from "../src/executor/guards";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

beforeEach(() => {
  document.body.innerHTML = "";
  document.title = "Sign in";
  // The guard asks whether a field is visible; happy-dom has no layout engine.
  restoreLayout = installLayout();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

function screen(html: string): void {
  document.body.innerHTML = html;
}

describe("recognising a verification-code page", () => {
  it("recognises an input marked one-time-code", () => {
    screen(`
      <h1>Check your email</h1>
      <form><input autocomplete="one-time-code" name="otp" /><button type="submit">Verify</button></form>
    `);
    const probe = codePromptInPage({});
    expect(probe.found).toBe(true);
    expect(probe.heading).toBe("Check your email");
    expect(probe.split).toBe(false);
  });

  it("recognises a numeric field whose label says Verification code", () => {
    screen(`
      <form>
        <label for="code">Verification code</label>
        <input id="code" inputmode="numeric" maxlength="6" />
      </form>
    `);
    expect(codePromptInPage({}).found).toBe(true);
  });

  it("recognises six split boxes", () => {
    screen(`
      <h2>Enter the code we sent</h2>
      <div class="boxes">
        ${[0, 1, 2, 3, 4, 5]
          .map((index) => `<input maxlength="1" aria-label="Digit ${index + 1}" />`)
          .join("")}
      </div>
    `);
    const probe = codePromptInPage({});
    expect(probe.found).toBe(true);
    expect(probe.split).toBe(true);
    expect(probe.heading).toContain("Enter the code");
  });

  it("leaves a password page as a login page", () => {
    screen(`
      <form>
        <input type="password" name="password" />
        <input autocomplete="one-time-code" />
      </form>
    `);
    expect(codePromptInPage({}).found).toBe(false);
  });

  it("does not treat a promo-code field as a verification code", () => {
    screen(`
      <form>
        <label for="promo">Promo code</label>
        <input id="promo" name="promo" />
      </form>
    `);
    expect(codePromptInPage({}).found).toBe(false);
  });

  it("does not use the URL's query string as the heading", () => {
    screen(`
      <h1>Verify it is you</h1>
      <form><input autocomplete="one-time-code" /></form>
    `);
    const probe = codePromptInPage({});
    expect(probe.heading).toBe("Verify it is you");
    expect(probe.heading).not.toContain("?");
  });
});

describe("typing the code a person supplied", () => {
  it("fills a single box and presses the submit button", () => {
    screen(`
      <h1>Check your email</h1>
      <form id="f">
        <input autocomplete="one-time-code" id="code" />
        <button type="submit" id="go">Verify</button>
      </form>
    `);
    let submitted = 0;
    document.getElementById("f")!.addEventListener("submit", (event) => {
      event.preventDefault();
      submitted += 1;
    });
    const result = enterCodeInPage({ code: "246810" });
    expect(result.entered).toBe(true);
    expect((document.getElementById("code") as HTMLInputElement).value).toBe("246810");
    expect(submitted).toBeGreaterThan(0);
  });

  it("fills six split boxes one digit each, then presses Continue", () => {
    screen(`
      <h2>Enter the code</h2>
      <form id="f">
        ${[0, 1, 2, 3, 4, 5]
          .map((index) => `<input maxlength="1" aria-label="Digit ${index + 1}" data-i="${index}" />`)
          .join("")}
        <button type="submit" id="go">Continue</button>
      </form>
    `);
    let submitted = 0;
    document.getElementById("f")!.addEventListener("submit", (event) => {
      event.preventDefault();
      submitted += 1;
    });
    const result = enterCodeInPage({ code: "246810" });
    expect(result.entered).toBe(true);
    const values = Array.from(document.querySelectorAll("input")).map(
      (input) => (input as HTMLInputElement).value,
    );
    expect(values).toEqual(["2", "4", "6", "8", "1", "0"]);
    expect(submitted).toBeGreaterThan(0);
  });

  it("presses Enter in the last box when there is no submit control", () => {
    screen(`
      <h2>Enter the code</h2>
      <div>
        ${[0, 1, 2, 3, 4, 5]
          .map((index) => `<input maxlength="1" aria-label="Digit ${index + 1}" />`)
          .join("")}
      </div>
    `);
    const keys: string[] = [];
    document.addEventListener("keydown", (event) => keys.push(event.key));
    const result = enterCodeInPage({ code: "246810" });
    expect(result.entered).toBe(true);
    expect(keys).toContain("Enter");
  });

  it("says not found when the page has no code field", () => {
    screen("<h1>Nothing here</h1>");
    expect(enterCodeInPage({ code: "246810" })).toEqual({
      entered: false,
      reason: "not_found",
    });
  });

  it("never puts the code in its answer", () => {
    screen(`
      <h1>Enter the code</h1>
      <form><input autocomplete="one-time-code" /><button type="submit">Verify</button></form>
    `);
    const result = enterCodeInPage({ code: "246810" });
    expect(JSON.stringify(result)).not.toContain("246810");
  });
});
