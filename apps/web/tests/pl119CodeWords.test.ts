// PL-119: the run page's words when no new code email arrived. The person may have opened the
// code in Gmail already, and Gmail's inbox feed lists only unread mail, so ProcessLens cannot see
// an opened message. Say that plainly instead of "no email has arrived yet".
import { describe, expect, it } from "vitest";

import { codeFromEmailWords } from "../src/lib/words";

describe("the words for a code that was not found", () => {
  it("says no new code email arrived, and why an opened one is invisible", () => {
    expect(codeFromEmailWords("none")).toBe(
      "No new code email arrived. If you already opened it in Gmail, ProcessLens can't see it — type the code here.",
    );
  });
});
