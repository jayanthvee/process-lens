// Deleting a stray step from the popup's live list (PL-043 part 2).
//
// A click recorded by mistake used to leave the person with one fix: record everything again.
// After Stop, each step line now carries a quiet × that asks the worker to remove that step and
// the evidence recorded right after it; while the recording is still running the × is hidden and
// the worker refuses the request.
import { beforeEach, describe, expect, it } from "vitest";
import { canRemoveStep, RecorderSession } from "../src/background/recorder-session";
import { REMOVE_STEP_LABEL, renderStepList, stepLines } from "../src/popup/flow";
import { stepCountWords } from "../src/popup/words";
import type { PendingEvent } from "../src/shared/events";

const STAMP = "2026-10-17T09:00:00.000Z";
const PAGE = "https://crm.example.test/leads";

function click(name: string): PendingEvent {
  return {
    at: STAMP,
    kind: "click",
    url: PAGE,
    step: {
      id: "click-1",
      action: "click",
      name,
      targets: [{ by: "role", role: "button", name }],
    },
  };
}

function fill(email: string): PendingEvent {
  return {
    at: STAMP,
    kind: "fill",
    url: PAGE,
    step: {
      id: "fill-1",
      action: "fill",
      targets: [{ by: "role", role: "textbox", name: "Email" }],
      value: { kind: "constant", value: email },
    },
  };
}

/** What the page showed after the action before it: its own event, with no step. */
function observation(text: string): PendingEvent {
  return { at: STAMP, kind: "observation", url: PAGE, step: null, observation: { text_visible: text } };
}

/**
 * The recording the person is looking at: a click, its evidence, a fill, the save click, and the
 * save's evidence. `seq` runs 1 to 5; the fill is the stray step in most tests.
 */
function recorded(): RecorderSession {
  const session = new RecorderSession();
  session.append(click("New lead"));
  session.append(observation("New lead"));
  session.append(fill("ravi@example.test"));
  session.append(click("Save"));
  session.append(observation("Lead created"));
  return session;
}

function list(): HTMLElement {
  const found = document.getElementById("steps");
  if (!found) throw new Error("the test markup is missing #steps");
  return found;
}

function removeButtons(): HTMLButtonElement[] {
  return Array.from(document.querySelectorAll<HTMLButtonElement>("#steps .steps__remove"));
}

beforeEach(() => {
  document.body.innerHTML = '<ol class="steps" id="steps"></ol>';
});

describe("RecorderSession.remove", () => {
  it("drops the step and the observation recorded right after it", () => {
    const session = recorded();
    const first = session.events[0]!;

    const removed = session.remove(first.seq);

    expect(removed).toMatchObject({ seq: first.seq, kind: "click" });
    // The observation that belonged to that click went with it; the rest is untouched.
    expect(session.events.map((event) => event.seq)).toEqual([3, 4, 5]);
    expect(session.count).toBe(3);
  });

  it("drops nothing else when the step has no observation of its own", () => {
    const session = recorded();
    const fillEvent = session.events[2]!;

    session.remove(fillEvent.seq);

    // The fill is not a click, so the next event is the save click and stays.
    expect(session.events.map((event) => event.seq)).toEqual([1, 2, 4, 5]);
    expect(session.count).toBe(4);
  });

  it("removes the last step and its observation, and leaves the earlier evidence", () => {
    const session = recorded();
    const save = session.events[3]!;

    session.remove(save.seq);

    expect(session.events.map((event) => event.seq)).toEqual([1, 2, 3]);
  });

  it("takes an observation attached to the action itself along, and nothing after it", () => {
    const session = new RecorderSession({ now: () => 1_000 });
    const save = session.append(click("Save"));
    session.noteLeaving();
    session.attachObservation(save.step!.id, { text_visible: "Lead created" });
    const next = session.append(click("Next"));

    session.remove(save.seq);

    expect(session.events.map((event) => event.seq)).toEqual([next.seq]);
    expect(session.events[0]!.observation).toBeUndefined();
  });

  it("changes nothing for a seq no event carries", () => {
    const session = recorded();
    const before = session.events.map((event) => event.seq);

    expect(session.remove(99)).toBeNull();

    expect(session.events.map((event) => event.seq)).toEqual(before);
    expect(session.count).toBe(5);
  });

  it("drops the evidence a removed step was still owed across a navigation", () => {
    const session = new RecorderSession({ now: () => 1_000 });
    const save = session.append(click("Save"));
    session.noteLeaving();
    expect(session.pendingTarget()).toBe(save.step!.id);

    session.remove(save.seq);

    expect(session.pendingTarget()).toBeNull();
  });

  it("is refused while the recording is still running", () => {
    expect(canRemoveStep("recording")).toBe(false);
    expect(canRemoveStep("idle")).toBe(true);
  });
});

describe("the × on a recorded step", () => {
  it("sits on every step line, and names what it does", () => {
    const session = recorded();
    renderStepList(list(), session.events, { recording: false });

    const items = Array.from(document.querySelectorAll("#steps .steps__item"));
    expect(items.map((item) => item.querySelector(".steps__text")?.textContent)).toEqual([
      "Clicked New lead",
      "Typed the email",
      "Clicked Save",
    ]);
    const buttons = removeButtons();
    expect(buttons).toHaveLength(3);
    for (const button of buttons) {
      expect(button.getAttribute("aria-label")).toBe(REMOVE_STEP_LABEL);
      expect(REMOVE_STEP_LABEL).toBe("Remove this step");
      expect(button.textContent).toBe("×");
      expect(button.type).toBe("button");
      // Quiet: it must not compete with the gold flow button.
      expect(button.classList.contains("button--primary")).toBe(false);
    }
  });

  it("is hidden while the recording is on, and shown after Stop", () => {
    const session = recorded();

    renderStepList(list(), session.events, { recording: true });
    expect(removeButtons()).toHaveLength(3);
    for (const button of removeButtons()) expect(button.hidden, "hidden while recording").toBe(true);

    renderStepList(list(), session.events, { recording: false });
    for (const button of removeButtons()) expect(button.hidden, "shown after Stop").toBe(false);
  });

  it("asks the worker to remove the step whose seq the line carries", () => {
    const session = recorded();
    const sent: { type: string; seq?: number }[] = [];
    renderStepList(list(), session.events, {
      recording: false,
      onRemove: (seq) => sent.push({ type: "record:remove", seq }),
    });

    const fillLine = stepLines(session.events)[1]!;
    removeButtons()[1]!.click();

    expect(fillLine.sentence).toBe("Typed the email");
    expect(sent).toEqual([{ type: "record:remove", seq: fillLine.seq }]);
    expect(fillLine.seq).toBe(session.events[2]!.seq);
  });

  it("re-renders the list and the count without the removed step", () => {
    const session = recorded();
    const redraw = (): void => {
      renderStepList(list(), session.events, {
        recording: false,
        onRemove: (seq) => {
          session.remove(seq);
          redraw();
        },
      });
    };
    redraw();

    removeButtons()[1]!.click();

    expect(stepLines(session.events).map((line) => line.sentence)).toEqual([
      "Clicked New lead",
      "Clicked Save",
    ]);
    expect(document.querySelectorAll("#steps .steps__item")).toHaveLength(2);
    expect(session.count).toBe(4);
    expect(stepCountWords(session.count)).toBe("4 steps recorded");
  });
});
