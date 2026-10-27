// PL-112 item 4: a recording that reaches a code page is marked as a sign-in task.
//
// The recorder's status carries the mark; starting a new recording and clearing an old one take it
// away again, and the worker sets it from the code-page message the recorder already sends.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { clearSignIn, markSignIn } from "../src/background/sign-in";
import { EMPTY_STATUS } from "../src/shared/messages";

const HERE = dirname(fileURLToPath(import.meta.url));

describe("the sign-in mark", () => {
  it("is set by a code page and keeps every other field", () => {
    const status = { ...EMPTY_STATUS, state: "recording" as const, count: 3 };

    const marked = markSignIn(status);

    expect(marked.signIn).toBe(true);
    expect(marked.state).toBe("recording");
    expect(marked.count).toBe(3);
  });

  it("is taken away by starting again or clearing the recording", () => {
    const marked = { ...EMPTY_STATUS, state: "idle" as const, signIn: true };

    expect(clearSignIn(marked).signIn).toBe(false);
    expect(clearSignIn(EMPTY_STATUS).signIn).toBe(false);
  });

  it("is wired to the recorder's code-page message and reset on start and clear", () => {
    const worker = readFileSync(resolve(HERE, "../src/background/service-worker.ts"), "utf8");

    expect(worker).toContain("markSignIn(status)");
    expect(worker.match(/clearSignIn\(/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
  });
});
