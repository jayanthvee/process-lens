// Post-action observation: what the page showed after a click or a submit.
//
// The recorder watches the page for a short window after an action and reports
// either a confirmation appearing or the URL moving, on its own `observation`
// event. These tests drive that window with fake timers and a real jsdom
// document, so they exercise the same MutationObserver and history hooks the
// extension runs in a browser.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { attachRecorder } from "../src/content/recorder";
import type { PendingEvent } from "../src/shared/events";
import { resetStepIds } from "../src/shared/events";

interface Harness {
  events: PendingEvent[];
  setActive: (value: boolean) => void;
  detach: () => void;
}

function harness(windowMs = 1500): Harness {
  const events: PendingEvent[] = [];
  let active = true;
  const detach = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => active,
    url: () => document.location.href,
    now: () => "2026-10-08T00:00:00.000Z",
    observationWindowMs: windowMs,
  });
  return {
    events,
    setActive: (value) => {
      active = value;
    },
    detach,
  };
}

/** Let MutationObserver microtasks run before the window's timers are advanced. */
function flushMutations(): Promise<void> {
  return new Promise((done) => {
    done();
  });
}

function alertInto(parentId: string, text: string, role = "alert"): void {
  const node = document.createElement("div");
  node.setAttribute("role", role);
  node.textContent = text;
  document.getElementById(parentId)!.appendChild(node);
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
  window.history.replaceState({}, "", "/leads");
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("post-action observation", () => {
  it("reports a confirmation that appears after a click", async () => {
    document.body.innerHTML = "<button id='save'>Save</button><div id='host'></div>";
    const recorded = harness();
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));
    expect(recorded.events).toHaveLength(1);

    alertInto("host", "Lead created");
    await flushMutations();
    await vi.advanceTimersByTimeAsync(1500);

    expect(recorded.events.map((event) => event.kind)).toEqual(["click", "observation"]);
    expect(recorded.events[1]).toMatchObject({
      kind: "observation",
      step: null,
      observation: { text_visible: "Lead created" },
    });
  });

  it("reports a URL change that fires no event, after a submit", async () => {
    document.body.innerHTML = "<form id='f'><button id='go' type='submit'>Save</button></form>";
    const recorded = harness();
    document.getElementById("f")!.dispatchEvent(new Event("submit", { bubbles: true }));

    window.history.pushState({}, "", "/leads/42");
    await vi.advanceTimersByTimeAsync(1500);

    expect(recorded.events.map((event) => event.kind)).toEqual(["submit", "observation"]);
    expect(recorded.events[1]).toMatchObject({
      kind: "observation",
      observation: { url_matches: "/leads/" },
    });
  });

  it("says nothing when the page does not change", async () => {
    document.body.innerHTML = "<button id='save'>Save</button>";
    const recorded = harness();
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));

    await vi.advanceTimersByTimeAsync(3000);

    expect(recorded.events.map((event) => event.kind)).toEqual(["click"]);
  });

  it("prefers a confirmation over a navigation", async () => {
    document.body.innerHTML = "<button id='save'>Save</button><div id='host'></div>";
    const recorded = harness();
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));

    window.history.pushState({}, "", "/leads/42");
    alertInto("host", "Saved", "status");
    await flushMutations();
    await vi.advanceTimersByTimeAsync(1500);

    expect(recorded.events[1]!.observation).toEqual({ text_visible: "Saved" });
  });

  it("ignores a confirmation element that was already on the page", async () => {
    document.body.innerHTML = "<div role='alert'>Old notice</div><button id='save'>Save</button>";
    const recorded = harness();
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));

    await vi.advanceTimersByTimeAsync(3000);

    expect(recorded.events.map((event) => event.kind)).toEqual(["click"]);
  });

  it("does not observe while recording is off", async () => {
    document.body.innerHTML = "<button id='save'>Save</button><div id='host'></div>";
    const recorded = harness();
    recorded.setActive(false);
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));

    alertInto("host", "Lead created");
    await flushMutations();
    await vi.advanceTimersByTimeAsync(3000);

    expect(recorded.events).toHaveLength(0);
  });

  it("stops observing when it is detached", async () => {
    document.body.innerHTML = "<button id='save'>Save</button><div id='host'></div>";
    const recorded = harness();
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));
    recorded.detach();

    alertInto("host", "Lead created");
    await flushMutations();
    await vi.advanceTimersByTimeAsync(3000);

    expect(recorded.events.map((event) => event.kind)).toEqual(["click"]);
  });

  it("never captures a secret in a confirmation", async () => {
    document.body.innerHTML = "<button id='save'>Save</button><div id='host'></div>";
    const recorded = harness();
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));

    alertInto("host", "Signed in for ravi@example.com");
    await flushMutations();
    await vi.advanceTimersByTimeAsync(1500);

    // The confirmation is page text, not a field value; it carries no step and
    // no target, so it can never widen what a recorded step points at.
    const observation = recorded.events[1]!;
    expect(observation.step).toBeNull();
    expect(observation.observation).toEqual({ text_visible: "Signed in for ravi@example.com" });
  });
});
