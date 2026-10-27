// PL-113 S2: the page the demonstration reached after the code page is recorded with the task.
//
// A sign-in recipe ends by waiting for that page, and the compiler can only write the wait when the
// teaching recorded it. The page the code was asked on and the page that follows it are two URLs
// of the recording, not a code: nothing here keeps, stores or shows a code.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { clearSignIn, markSignIn, noteSignInPage } from "../src/background/sign-in";
import { EMPTY_STATUS } from "../src/shared/messages";

const HERE = dirname(fileURLToPath(import.meta.url));
const CODE_PAGE = "https://app.example.test/verify";
const HOME_PAGE = "https://app.example.test/home";

describe("the page after the code page (PL-113 S2)", () => {
  it("is recorded once, from the first page after the code page", () => {
    const asked = noteSignInPage(markSignIn({ ...EMPTY_STATUS, state: "recording" }), CODE_PAGE);

    expect(asked.signInCodeUrl).toBe(CODE_PAGE);
    expect(asked.signInAfterUrl).toBeUndefined();

    const landed = noteSignInPage(asked, HOME_PAGE);
    expect(landed.signInAfterUrl).toBe(HOME_PAGE);

    // A later page does not move it: the wait is for the page right after the code.
    expect(noteSignInPage(landed, `${HOME_PAGE}/inbox`).signInAfterUrl).toBe(HOME_PAGE);
  });

  it("stays empty on the code page itself, and for a recording that is not a sign-in", () => {
    const asked = noteSignInPage(markSignIn({ ...EMPTY_STATUS, state: "recording" }), CODE_PAGE);

    expect(noteSignInPage(asked, CODE_PAGE).signInAfterUrl).toBeUndefined();
    expect(noteSignInPage({ ...EMPTY_STATUS, state: "recording" }, HOME_PAGE)).toEqual({
      ...EMPTY_STATUS,
      state: "recording",
    });
  });

  it("is taken away by starting again or clearing the recording", () => {
    const landed = noteSignInPage(
      noteSignInPage(markSignIn({ ...EMPTY_STATUS, state: "recording" }), CODE_PAGE),
      HOME_PAGE,
    );

    const cleared = clearSignIn(landed);
    expect(cleared.signIn).toBe(false);
    expect(cleared.signInAfterUrl).toBeUndefined();
    expect(cleared.signInCodeUrl).toBeUndefined();
  });

  it("is wired to the worker's recorded events", () => {
    const worker = readFileSync(resolve(HERE, "../src/background/service-worker.ts"), "utf8");

    expect(worker).toContain("noteSignInPage(");
    // The page's own address is what the sender carries, never a code.
    expect(worker.match(/noteSignInPage\(/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
  });
});
