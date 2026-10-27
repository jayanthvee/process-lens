// PL-112 item 2: a refusal never contradicts the person's save choice.
import { describe, expect, it } from "vitest";

import { compileRefusalSentence } from "../src/popup/words";
import type { RecordedEvent } from "../src/shared/recipe-types";

const EVENTS: RecordedEvent[] = [
  {
    seq: 1,
    at: "2026-10-26T10:00:00Z",
    kind: "fill",
    url: "https://app.example.test/login",
    step: {
      id: "fill-1",
      action: "fill",
      targets: [{ by: "role", role: "textbox", name: "Email" }],
      value: { kind: "constant", value: "a@example.com" },
    },
  },
  {
    seq: 2,
    at: "2026-10-26T10:00:01Z",
    kind: "click",
    url: "https://app.example.test/login",
    step: {
      id: "click-2",
      action: "click",
      targets: [{ by: "role", role: "button", name: "Continue" }],
    },
  },
];

const NO_ASSERTION = [
  "commit step click-2 has no assert_after: the recording observed no confirmation and no "
    + "navigation, and none was supplied for it",
];

describe("a refusal under 'Nothing gets saved'", () => {
  it("never tells the person to choose what they already chose", () => {
    const sentence = compileRefusalSentence(NO_ASSERTION, EVENTS, { nothingSaved: true });

    expect(sentence.startsWith("This recording can't be saved yet:")).toBe(true);
    expect(sentence.toLowerCase()).not.toContain("choose “nothing gets saved”");
    expect(sentence.toLowerCase()).not.toContain("pick a different answer");
  });

  it("keeps the click's own sentence when a click was named", () => {
    const sentence = compileRefusalSentence(NO_ASSERTION, EVENTS, { nothingSaved: false });

    expect(sentence).toContain("Continue");
    expect(sentence).toContain("Nothing gets saved");
  });
});
