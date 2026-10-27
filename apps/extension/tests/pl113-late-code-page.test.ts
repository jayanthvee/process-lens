// PL-113 S1: a code page that arrives late is still noticed while teaching.
//
// A site answers Continue with a loading state and a server call, so its code boxes render well
// after the 250 ms settle the recorder used to wait. Typing the code is never recorded (correctly),
// so no later action ever probed again -- the recording was saved as a spreadsheet task with no
// email pick, and the panel never offered "Reading your code from Gmail...".
//
// The watch below is what the recorder runs after each recorded action: it keeps looking for as
// long as the page is still changing, up to 15 s after the last action, and stops at the first code
// page found. The markup is ordinary sign-in markup, not one site's: six "Digit N of 6" boxes.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CODE_WATCH_WINDOW_MS, watchCodePage } from "../src/content/code-watch";
import { codePromptInPage, enterCodeInPage } from "../src/shared/code-page";
import { installLayout } from "./helpers/layout";

const HERE = dirname(fileURLToPath(import.meta.url));
const T0 = Date.parse("2026-10-27T09:00:00Z");
const SITE = "https://app.example.test/login";

let restoreLayout: (() => void) | null = null;
let watch: { stop: () => void } | null = null;

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = "";
  restoreLayout = installLayout();
});

afterEach(() => {
  watch?.stop();
  watch = null;
  restoreLayout?.();
  restoreLayout = null;
  vi.useRealTimers();
});

/** Let MutationObserver's microtask and the watcher's async probe run. */
async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

/** A sign-in page, with Continue already clicked and nothing on the page but the email box. */
function signInPage(): void {
  document.body.innerHTML = `
    <h1>Sign in</h1>
    <form id="f">
      <label for="email">Email</label>
      <input id="email" type="email" />
      <button type="submit" id="continue">Continue</button>
    </form>`;
}

/** The code page the site renders 1.5 s after Continue, without any further action. */
function showCodeBoxesLate(): void {
  document.body.innerHTML = `
    <h1>Check your email</h1>
    <p>We sent a six digit code to your inbox.</p>
    <form id="code">
      ${Array.from(
        { length: 6 },
        (_, index) =>
          `<label for="d${index}">Digit ${index + 1} of 6</label>` +
          `<input id="d${index}" maxlength="1" inputmode="numeric" aria-label="Digit ${index + 1} of 6" />`,
      ).join("")}
      <button type="submit">Verify</button>
    </form>`;
}

function deps(overrides: Partial<Parameters<typeof watchCodePage>[1]> = {}) {
  const ask = vi.fn(async () => ({ status: "found" as const, code: "482913" }));
  return {
    ask,
    deps: {
      probe: codePromptInPage,
      enter: enterCodeInPage,
      ask,
      report: () => undefined,
      quietly: <T,>(work: () => T): T => work(),
      now: () => T0,
      site: () => SITE,
      lastActionAt: () => T0,
      ...overrides,
    },
  };
}

describe("a code page that arrives after the action (PL-113 S1)", () => {
  it("is found 1.5 s later, and the code is asked for once", async () => {
    signInPage();
    const { ask, deps: watchDeps } = deps();
    watch = watchCodePage(document, watchDeps, { attempted: false });

    // The click was recorded at T0; the site takes 1.5 s to render its boxes.
    await vi.advanceTimersByTimeAsync(1_500);
    showCodeBoxesLate();
    await flush();
    await vi.advanceTimersByTimeAsync(400);
    await flush();

    expect(ask).toHaveBeenCalledTimes(1);
    // The lookup starts from the last recorded action (minus 30 s of slack), not from the probe,
    // so a code email that arrived before the page settled is still found.
    expect(ask.mock.calls[0]?.[0]).toMatchObject({ since: T0 - 30_000, site: SITE });
  });

  it("keeps looking while the page is still changing, and stops after 15 s", async () => {
    signInPage();
    const { ask, deps: watchDeps } = deps();
    watch = watchCodePage(document, watchDeps, { attempted: false });

    // The page changes several times and only then shows its boxes: nothing may be missed just
    // because a fixed settle window passed.
    for (let change = 0; change < 5; change += 1) {
      await vi.advanceTimersByTimeAsync(2_000);
      document.body.insertAdjacentHTML("beforeend", `<div>loading ${change}</div>`);
      await flush();
    }
    showCodeBoxesLate();
    await flush();
    await vi.advanceTimersByTimeAsync(400);
    await flush();

    expect(ask).toHaveBeenCalledTimes(1);

    // The watch is over 15 s after the last action: a page that changes then is not probed.
    await vi.advanceTimersByTimeAsync(CODE_WATCH_WINDOW_MS);
    document.body.insertAdjacentHTML("beforeend", "<div>a change long after</div>");
    await flush();
    await vi.advanceTimersByTimeAsync(2_000);
    await flush();

    expect(ask).toHaveBeenCalledTimes(1);
  });

  it("does not ask twice for the same code page", async () => {
    signInPage();
    const { ask, deps: watchDeps } = deps({
      ask: vi.fn(async () => ({ status: "none" as const })),
    });
    showCodeBoxesLate();
    watch = watchCodePage(document, watchDeps, { attempted: false });

    await flush();
    await vi.advanceTimersByTimeAsync(400);
    await flush();
    document.body.insertAdjacentHTML("beforeend", "<div>still here</div>");
    await flush();
    await vi.advanceTimersByTimeAsync(400);
    await flush();

    expect(ask).toHaveBeenCalledTimes(1);
  });

  it("is what the recorder runs after an action", () => {
    const recorder = readFileSync(resolve(HERE, "../src/content/recorder.ts"), "utf8");

    expect(recorder).toContain("watchCodePage(");
    // The single 250 ms probe is gone: it is the reason a late page was missed.
    expect(recorder).not.toContain("TEACH_CODE_SETTLE_MS");
  });
});
