/** The plain-words vocabulary: the mapping table, the reasons, and the two word forms. */

import { describe, expect, it } from "vitest";

import {
  CONNECT_CHROME,
  durationWords,
  isOpenStatus,
  minutesSavedWords,
  pauseReasonWords,
  plainReason,
  relativeTime,
  rowOutcome,
  runActions,
  runStatusWords,
  siteCapReached,
  siteCapWords,
} from "../src/lib/words";

describe("rowOutcome", () => {
  it("maps every state of the ledger to the design's words", () => {
    const expected: Array<[string, string, string]> = [
      ["pending", "Waiting", "idle"],
      ["claimed", "Waiting", "idle"],
      ["prechecked", "Waiting", "idle"],
      ["filling", "Waiting", "idle"],
      ["submitting", "Checking…", "live"],
      ["submitted_unverified", "Checking…", "live"],
      ["reconciling", "Checking…", "live"],
      ["verified", "Done", "ok"],
      ["skipped", "Already there", "idle"],
      ["parked", "Needs your help", "help"],
      ["failed", "Didn't work", "help"],
    ];

    for (const [state, label, tone] of expected) {
      expect(rowOutcome(state), state).toEqual({ label, tone });
    }
  });

  it("falls back to Waiting for a state it does not know", () => {
    expect(rowOutcome("something_new")).toEqual({ label: "Waiting", tone: "idle" });
  });

  it("never shows the engine's own word on screen", () => {
    for (const state of ["parked", "verified", "submitting", "submitted_unverified", "reconciling"]) {
      expect(rowOutcome(state).label.toLowerCase()).not.toContain(state);
    }
  });
});

describe("runStatusWords", () => {
  it("says Waiting for Chrome when a run is queued and no Chrome session is attached", () => {
    expect(runStatusWords("queued", false)).toEqual({
      label: "Waiting for Chrome. Click the ProcessLens icon in Chrome's toolbar to connect.",
      tone: "idle",
    });
    // With Chrome attached, or while the session is not known yet, the plain status stands.
    expect(runStatusWords("queued", true)).toEqual({ label: "Queued", tone: "idle" });
    expect(runStatusWords("queued")).toEqual({ label: "Queued", tone: "idle" });
    expect(runStatusWords("queued", undefined)).toEqual({ label: "Queued", tone: "idle" });
  });
  it("maps every run status to the design's words", () => {
    expect(runStatusWords("queued")).toEqual({ label: "Queued", tone: "idle" });
    expect(runStatusWords("running")).toEqual({ label: "Running", tone: "live" });
    expect(runStatusWords("paused")).toEqual({ label: "Paused", tone: "help" });
    expect(runStatusWords("completed")).toEqual({ label: "Finished", tone: "ok" });
    expect(runStatusWords("cancelled")).toEqual({ label: "Stopped", tone: "idle" });
    expect(runStatusWords("failed")).toEqual({ label: "Didn't work", tone: "help" });
  });
});

describe("plainReason", () => {
  it("turns each reason the runner writes into one sentence", () => {
    expect(plainReason("attempts exhausted")).toBe("It didn't work after three tries.");
    expect(plainReason("already committed in an earlier run")).toBe(
      "It was already added in an earlier run.",
    );
    expect(plainReason("run cancelled")).toBe("The run was stopped before this row was reached.");
    expect(plainReason("dismissed by a person")).toBe("You dismissed this row.");
    expect(plainReason("duplicate")).toBe("It was already there.");
  });

  it("shows a reason the runner wrote for a person as it stands, and never a missing one", () => {
    expect(plainReason("The salesperson isn't in Odoo.")).toBe("The salesperson isn't in Odoo.");
    expect(plainReason("A value it needs is missing from the row.")).toBe(
      "A value it needs is missing from the row.",
    );
    // A hyphen is part of the host, not a word break (PL-106 item 7).
    expect(plainReason("Not found in my-crm.example.com.")).toBe(
      "Not found in my-crm.example.com.",
    );
    expect(plainReason(null)).toBe("Something went wrong.");
    expect(plainReason("   ")).toBe("Something went wrong.");
  });

  it("never lets an identifier reach the screen, even for a code it has never seen", () => {
    expect(plainReason("some_new_code")).toBe("Some new code.");
  });
});

describe("pauseReasonWords", () => {
  it("says what a person can do about a pause", () => {
    expect(pauseReasonWords("paused_by_operator")).toBe(
      "You paused this run. Resume when you are ready.",
    );
    expect(pauseReasonWords("login_required")).toBe(
      "Log in to the site in the ProcessLens window, then resume.",
    );
    expect(pauseReasonWords("challenge")).toBe(
      "Solve the security challenge the site is showing in the ProcessLens window, then resume.",
    );
    expect(pauseReasonWords("awaiting_approval")).toBe(
      "An approval request is waiting for your decision, then resume.",
    );
    expect(pauseReasonWords("rate_limited")).toBe(
      "The site is rate limiting us. Wait a moment, then resume.",
    );
    expect(pauseReasonWords("destination_unreachable")).toBe(
      "The site could not be reached. Check it, then resume.",
    );
  });

  it("PL-040: the runner's own pause reasons read as words, never as codes", () => {
    // PL-104: the run page has one button for this, right under the reason.
    expect(pauseReasonWords("window_hidden")).toContain("Press Show it and continue.");
    // PL-106 item 8: the Chrome sentence says where the click is, not "press Connect".
    expect(pauseReasonWords("executor_disconnected")).toContain("Chrome disconnected");
    expect(pauseReasonWords("canary_failed")).toContain("The first row didn't work");
    for (const reason of [
      "window_hidden",
      "executor_disconnected",
      "canary_failed",
      "policy_blocked",
      "duplicate_detected",
      "records_not_exit",
      "internal_error",
    ]) {
      expect(pauseReasonWords(reason)).not.toContain("Sort that out");
    }
  });

  it("accepts the older spelling and the case the API happens to send", () => {
    expect(pauseReasonWords("operator_paused")).toBe(
      "You paused this run. Resume when you are ready.",
    );
    expect(pauseReasonWords("  Login_Required ")).toBe(
      "Log in to the site in the ProcessLens window, then resume.",
    );
  });

  it("writes a reason it has never seen as a sentence, not an identifier", () => {
    expect(pauseReasonWords("network_error")).toBe(
      "Paused: Network error. Sort that out, then resume.",
    );
  });

  it("answers null when the run is not paused for a reason", () => {
    expect(pauseReasonWords(null)).toBeNull();
    expect(pauseReasonWords("  ")).toBeNull();
  });
});

describe("runActions and isOpenStatus", () => {
  it("offers the right buttons for each status", () => {
    expect(runActions("running")).toEqual({ pause: true, resume: false, cancel: true });
    expect(runActions("queued")).toEqual({ pause: true, resume: false, cancel: true });
    expect(runActions("paused")).toEqual({ pause: true, resume: true, cancel: true });
    expect(runActions("completed")).toEqual({ pause: false, resume: false, cancel: false });
    expect(runActions("failed")).toEqual({ pause: false, resume: false, cancel: false });
  });

  it("counts queued, running and paused as open", () => {
    expect(isOpenStatus("queued")).toBe(true);
    expect(isOpenStatus("running")).toBe(true);
    expect(isOpenStatus("paused")).toBe(true);
    expect(isOpenStatus("completed")).toBe(false);
    expect(isOpenStatus("cancelled")).toBe(false);
    expect(isOpenStatus("failed")).toBe(false);
    expect(isOpenStatus(null)).toBe(false);
  });
});

describe("relativeTime", () => {
  const now = new Date("2026-10-15T12:00:00Z");

  it("says when something happened, in words", () => {
    expect(relativeTime("2026-10-15T11:59:30Z", now)).toBe("Now");
    expect(relativeTime("2026-10-15T11:58:00Z", now)).toBe("2 minutes ago");
    expect(relativeTime("2026-10-15T11:00:00Z", now)).toBe("1 hour ago");
    expect(relativeTime("2026-10-15T10:00:00Z", now)).toBe("2 hours ago");
    expect(relativeTime("2026-10-14T11:00:00Z", now)).toBe("Yesterday");
    expect(relativeTime("2026-10-12T11:00:00Z", now)).toBe("3 days ago");
  });

  it("says Never when there is no time at all", () => {
    expect(relativeTime(null, now)).toBe("Never");
    expect(relativeTime("", now)).toBe("Never");
    expect(relativeTime("not a date", now)).toBe("Never");
  });
});

describe("durationWords and minutesSavedWords", () => {
  it("writes minutes as hours and minutes, and never goes negative", () => {
    expect(durationWords(372)).toBe("6 h 12 m");
    expect(durationWords(60)).toBe("1 h");
    expect(durationWords(45)).toBe("45 m");
    expect(durationWords(0)).toBe("0 m");
    expect(durationWords(-30)).toBe("0 m");
  });

  it("says how much time was saved, the way a person would", () => {
    expect(minutesSavedWords(52)).toBe("About 52 minutes saved");
    expect(minutesSavedWords(60)).toBe("About 1 hour saved");
    expect(minutesSavedWords(125)).toBe("About 2 hours saved");
    expect(minutesSavedWords(1)).toBe("About 1 minute saved");
    expect(minutesSavedWords(0)).toBe("Nothing saved yet");
    expect(minutesSavedWords(-5)).toBe("Nothing saved yet");
  });
});

describe("the site daily cap (PL-067)", () => {
  it("names the site, its limit and tomorrow in one sentence", () => {
    expect(
      siteCapWords({ origin: "https://www.linkedin.com/", limit: 25, used: 25 }),
    ).toBe(
      "Paused: today's limit for linkedin.com (25 rows) is reached. It can continue tomorrow.",
    );
  });

  it("keeps Resume disabled while the count stands, and only then", () => {
    expect(siteCapReached({ origin: "https://x.example", limit: 25, used: 25 })).toBe(true);
    expect(siteCapReached({ origin: "https://x.example", limit: 25, used: 24 })).toBe(false);
  });
});

describe("a finished run that left rows for a person (PL-106 item 4)", () => {
  it("says how many need you instead of reading like a clean finish", () => {
    expect(runStatusWords("completed", undefined, { verified: 11, parked: 3, failed: 1 })).toEqual({
      label: "Finished — 4 need you",
      tone: "help",
    });
    expect(runStatusWords("completed", undefined, { verified: 14, parked: 1 })).toEqual({
      label: "Finished — 1 needs you",
      tone: "help",
    });
  });

  it("stays plain Finished when every row is done or was already there", () => {
    expect(runStatusWords("completed", undefined, { verified: 4, skipped: 6 })).toEqual({
      label: "Finished",
      tone: "ok",
    });
  });
});

describe("a row whose record the search did not find (PL-106 item 7)", () => {
  it("waits for a person until they skip it, then reads Skipped not Already there", () => {
    // The branch parks: an absent result is not proof, so the row comes to the run page first.
    expect(rowOutcome("parked", "Not found in crm.example.com.")).toEqual({
      label: "Needs your help",
      tone: "help",
    });
    expect(rowOutcome("skipped", "Not found in crm.example.com.")).toEqual({
      label: "Skipped",
      tone: "idle",
    });
    expect(plainReason("Not found in crm.example.com.", "skipped")).toBe(
      "Not found in crm.example.com.",
    );
  });

  it("keeps Already there for a row that was skipped because it was there", () => {
    expect(rowOutcome("skipped", "It was already there.")).toEqual({
      label: "Already there",
      tone: "idle",
    });
    expect(rowOutcome("skipped")).toEqual({ label: "Already there", tone: "idle" });
  });
});

describe("what to do when Chrome is not connected (PL-106 item 8)", () => {
  it("says where to click, wherever Chrome's absence is reported", () => {
    expect(CONNECT_CHROME).toBe("Click the ProcessLens icon in Chrome's toolbar to connect.");
    expect(runStatusWords("queued", false).label).toContain(CONNECT_CHROME);
    expect(pauseReasonWords("executor_disconnected")).toContain(CONNECT_CHROME);
  });
});
