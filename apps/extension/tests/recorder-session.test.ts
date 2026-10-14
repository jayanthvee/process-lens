// The recording session: what only the service worker can know, because only it lives through
// a navigation — the step ids (requirement 5), which navigations belong in the recording
// (requirement 6), and the action a navigating document is still owed evidence for
// (requirement 3).
import { describe, expect, it } from "vitest";
import {
  ARRIVAL_TARGET_WINDOW_MS,
  RecorderSession,
  shouldRecordNavigation,
  type NavigationDetails,
} from "../src/background/recorder-session";
import { shouldResetStepIds } from "../src/content/recorder";
import { nextStepId, resetStepIds, type PendingEvent } from "../src/shared/events";

function clickEvent(provisionalId: string, url = "https://mail.example.test/inbox"): PendingEvent {
  return {
    at: "2026-10-12T00:00:00.000Z",
    kind: "click",
    url,
    step: {
      id: provisionalId,
      action: "click",
      name: "Click Save",
      targets: [{ by: "role", role: "button", name: "Save" }],
    },
  };
}

function navigation(overrides: Partial<NavigationDetails> = {}): NavigationDetails {
  return { frameId: 0, transitionType: "typed", transitionQualifiers: [], ...overrides };
}

describe("step ids belong to the session (requirement 5)", () => {
  it("keeps counting across page loads, so a reload cannot restart the counter", () => {
    const session = new RecorderSession();

    // Page one records a click; the content script's own counter therefore reads "click-1".
    session.append(clickEvent("click-1"));
    // Page two is a new document: its counter starts at one again, which is exactly how the
    // old recorder minted duplicate ids.
    session.append(clickEvent("click-1"));
    session.append(clickEvent("click-2"));

    expect(session.events.map((event) => event.step?.id)).toEqual(["click-1", "click-2", "click-3"]);
  });

  it("counts each action separately", () => {
    const session = new RecorderSession();
    session.append(clickEvent("click-1"));
    session.append({
      at: "2026-10-12T00:00:00.000Z",
      kind: "fill",
      url: "https://mail.example.test/inbox",
      step: {
        id: "fill-1",
        action: "fill",
        name: "Fill Email",
        targets: [{ by: "role", role: "textbox", name: "Email" }],
        value: { kind: "constant", value: "ana@example.test" },
      },
    });
    session.append(clickEvent("click-1"));

    expect(session.events.map((event) => event.step?.id)).toEqual(["click-1", "fill-1", "click-2"]);
  });

  it("restores its counters after the worker restarts, instead of re-minting ids", () => {
    const first = new RecorderSession();
    first.append(clickEvent("click-1"));
    first.append(clickEvent("click-1"));
    first.append(clickEvent("click-1"));

    const restarted = new RecorderSession();
    restarted.restore(first.events);
    expect(restarted.append(clickEvent("click-1")).step?.id).toBe("click-4");
    expect(restarted.count).toBe(4);
  });

  it("keeps the recorded order in seq, whatever the pages numbered", () => {
    const session = new RecorderSession();
    const first = session.append(clickEvent("click-1"));
    const second = session.append(clickEvent("click-1"));
    expect([first.seq, second.seq]).toEqual([1, 2]);
  });
});

describe("navigations (requirement 6)", () => {
  const following = { recording: true, recordedTab: true };

  it("records a back/forward as a navigate event with its URL", () => {
    expect(
      shouldRecordNavigation(
        navigation({ transitionType: "link", transitionQualifiers: ["forward_back"] }),
        following,
      ),
    ).toBe(true);

    const session = new RecorderSession();
    const event = session.appendNavigate("https://mail.example.test/leads?page=2");

    expect(event).toMatchObject({
      kind: "navigate",
      step: null,
      url: "https://mail.example.test/leads?page=2",
    });
    expect(event.observation).toBeUndefined();
  });

  it("records a typed URL, a reload and a bookmark", () => {
    for (const transitionType of ["typed", "reload", "auto_bookmark"]) {
      expect(shouldRecordNavigation(navigation({ transitionType }), following)).toBe(true);
    }
  });

  it("does not record the navigation a link click caused", () => {
    expect(shouldRecordNavigation(navigation({ transitionType: "link" }), following)).toBe(false);
  });

  it("does not record the navigation a form submit caused", () => {
    expect(
      shouldRecordNavigation(navigation({ transitionType: "form_submit" }), following),
    ).toBe(false);
  });

  it("does not record a script or a redirect", () => {
    for (const transitionType of ["generated", "auto_subframe", "other"]) {
      expect(shouldRecordNavigation(navigation({ transitionType }), following)).toBe(false);
    }
  });

  it("does not record a subframe, another tab, or anything while recording is off", () => {
    expect(shouldRecordNavigation(navigation({ frameId: 3 }), following)).toBe(false);
    expect(
      shouldRecordNavigation(navigation(), { recording: true, recordedTab: false }),
    ).toBe(false);
    expect(
      shouldRecordNavigation(navigation(), { recording: false, recordedTab: true }),
    ).toBe(false);
  });
});

describe("the action a navigation is owed evidence for (requirement 3)", () => {
  it("keeps the unobserved click as the target of the page that lands", () => {
    const session = new RecorderSession({ now: () => 1_000 });
    session.append(clickEvent("click-1"));

    expect(session.noteLeaving()).toBe(true);
    expect(session.pendingTarget()).toBe("click-1");

    const updated = session.attachObservation("click-1", { text_visible: "Lead created" });

    expect(updated?.observation).toEqual({ text_visible: "Lead created" });
    expect(session.events.filter((event) => event.observation).length).toBe(1);
    expect(session.pendingTarget()).toBeNull();
  });

  it("owes nothing when the action was already observed", () => {
    const session = new RecorderSession({ now: () => 1_000 });
    session.append(clickEvent("click-1"));
    session.noteLeaving();
    session.attachObservation("click-1", { text_visible: "Lead created" });

    expect(session.noteLeaving()).toBe(false);
    expect(session.pendingTarget()).toBeNull();
  });

  it("only takes a report for the step it is waiting on", () => {
    const session = new RecorderSession({ now: () => 1_000 });
    session.append(clickEvent("click-1"));
    session.noteLeaving();

    expect(session.attachObservation("click-2", { text_visible: "Something else" })).toBeNull();
    expect(session.events[0]!.observation).toBeUndefined();
    expect(session.pendingTarget()).toBe("click-1");
  });

  it("drops a target the arriving page never came back for", () => {
    let clock = 1_000;
    const session = new RecorderSession({ now: () => clock });
    session.append(clickEvent("click-1"));
    session.noteLeaving();

    clock += ARRIVAL_TARGET_WINDOW_MS + 1;

    expect(session.pendingTarget()).toBeNull();
    expect(session.attachObservation("click-1", { text_visible: "Too late" })).toBeNull();
  });

  it("owes nothing to the previous demonstration once a new one starts", () => {
    const session = new RecorderSession({ now: () => 1_000 });
    session.append(clickEvent("click-1"));
    session.noteLeaving();

    session.forgetArrival();

    expect(session.pendingTarget()).toBeNull();
  });

  it("hands the target to one page only", () => {
    const session = new RecorderSession({ now: () => 1_000 });
    session.append(clickEvent("click-1"));
    session.noteLeaving();

    // The page the click navigated to asks first and gets it; every other asker — another
    // frame, a later load in the same tab — is owed nothing.
    expect(session.takePendingTarget()).toBe("click-1");
    expect(session.takePendingTarget()).toBeNull();
    expect(session.takePendingTarget()).toBeNull();

    // And the page that was handed it can still report.
    const updated = session.attachObservation("click-1", { text_visible: "Lead created" });
    expect(updated?.observation).toEqual({ text_visible: "Lead created" });
  });

  it("survives a worker restart", () => {
    const first = new RecorderSession({ now: () => 1_000 });
    first.append(clickEvent("click-1"));
    first.noteLeaving();

    const restarted = new RecorderSession({ now: () => 1_100 });
    restarted.restore(first.events);
    restarted.restoreArrival(first.snapshotArrival());

    expect(restarted.takePendingTarget()).toBe("click-1");
    expect(restarted.attachObservation("click-1", { text_visible: "Lead created" })).not.toBeNull();
  });

  it("forgets everything on clear", () => {
    const session = new RecorderSession({ now: () => 1_000 });
    session.append(clickEvent("click-1"));
    session.noteLeaving();

    session.clear();

    expect(session.count).toBe(0);
    expect(session.pendingTarget()).toBeNull();
    expect(session.append(clickEvent("click-1")).step?.id).toBe("click-1");
  });
});

describe("step ids restart for a new recording (PL-018, requirement 4)", () => {
  it("translates an envelope reference to the stored step id (PL-018, the supersedes fold)", () => {
    const session = new RecorderSession();
    // The page mints `fill-2` from its own global counter; the session stores that fill as `fill-1`.
    const prefix = session.append({
      at: "2026-10-12T00:00:00.000Z",
      kind: "fill",
      url: "https://mail.example.test/inbox",
      step: {
        id: "fill-2",
        action: "fill",
        name: "Fill To",
        targets: [{ by: "role", role: "combobox", name: "To" }],
        value: { kind: "constant", value: "dana" },
      },
    });
    expect(prefix.step?.id).toBe("fill-1");

    const chip = session.append({
      at: "2026-10-12T00:00:00.000Z",
      kind: "fill",
      url: "https://mail.example.test/inbox",
      step: {
        id: "fill-4",
        action: "fill",
        name: "Fill To",
        targets: [{ by: "role", role: "combobox", name: "To" }],
        value: { kind: "constant", value: "dana@example.test" },
      },
      chip: true,
      autocomplete: true,
      supersedes: "fill-2",
    });

    // The reference must name the id the stored fill actually carries, or the compiler's fold
    // cannot find the prefix and the accept stays a press of its own.
    expect(chip.supersedes).toBe("fill-1");
    expect(session.events[0]!.step?.id).toBe(chip.supersedes);
  });

  it("restarts the ids when a recording starts and when the recording is cleared", () => {
    // A new recording: the previous demonstration's numbers mean nothing to it.
    expect(shouldResetStepIds({ state: "recording", count: 0 }, { state: "idle", count: 0 })).toBe(
      true,
    );
    // The same recording reporting again, and a stop: the ids belong to the session that is still
    // there (the service worker renumbers what it holds), so neither restarts them.
    expect(shouldResetStepIds({ state: "recording", count: 3 }, { state: "recording", count: 3 })).toBe(
      false,
    );
    expect(shouldResetStepIds({ state: "idle", count: 3 }, { state: "recording", count: 3 })).toBe(
      false,
    );
    // A clear: idle, with nothing recorded any more — however it got there.
    expect(shouldResetStepIds({ state: "idle", count: 0 }, { state: "recording", count: 4 })).toBe(
      true,
    );
    expect(shouldResetStepIds({ state: "idle", count: 0 }, { state: "idle", count: 5 })).toBe(true);
  });

  it("starts a second recording's ids at one again", () => {
    resetStepIds();
    expect(nextStepId("click")).toBe("click-1");
    nextStepId("fill");
    expect(nextStepId("click")).toBe("click-3");

    // The transition into recording is what the content script resets on.
    const transition = { state: "recording", count: 0 };
    expect(shouldResetStepIds(transition, { state: "idle", count: 0 })).toBe(true);
    resetStepIds();

    expect(nextStepId("click")).toBe("click-1");
    expect(nextStepId("fill")).toBe("fill-2");
  });
});
