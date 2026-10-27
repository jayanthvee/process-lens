/**
 * PL-113 item 6, as corrected by T1's review (B2): a row whose search found nothing parks, says
 * which value was not found, and offers the one-click **Skip it**.
 *
 * The sentence changed from "Not found in <site>." to "No <column> called ‘<value>’ was found.",
 * so everything that used to read the old prefix has to read the new shape too: the action label,
 * the reason sent with the skip, and the row's outcome and words.
 */

import { describe, expect, it } from "vitest";

import { actionReason, recordActionLabel } from "../src/pages/needsHelpActions";
import { plainReason, rowOutcome } from "../src/lib/words";

const NOTHING_FOUND = "No sku called ‘Ceramic Coffee Mug’ was found.";

describe("a row whose search found nothing (PL-113 item 6, B2)", () => {
  it("offers Skip it, and sends the sentence back with the skip", () => {
    expect(recordActionLabel("dismiss", NOTHING_FOUND)).toBe("Skip it");
    expect(recordActionLabel("requeue", NOTHING_FOUND)).toBe("Try again");
    expect(actionReason("dismiss", NOTHING_FOUND)).toBe(NOTHING_FOUND);
  });

  it("waits for a person while parked, then reads Skipped", () => {
    expect(rowOutcome("parked", NOTHING_FOUND)).toEqual({
      label: "Needs your help",
      tone: "help",
    });
    expect(rowOutcome("skipped", NOTHING_FOUND)).toEqual({ label: "Skipped", tone: "idle" });
  });

  it("shows the sentence exactly as written, curly quotes and all", () => {
    expect(plainReason(NOTHING_FOUND)).toBe(NOTHING_FOUND);
    expect(plainReason(NOTHING_FOUND, "skipped")).toBe(NOTHING_FOUND);
  });

  it("still leaves an ordinary reason alone", () => {
    expect(recordActionLabel("dismiss", "missing email")).toBe("Dismiss");
  });
});
