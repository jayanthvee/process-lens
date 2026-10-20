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
      // PL-027: the record id keeps its shape. Dropping the segment would leave "/leads/", which
      // the page the submit was sent from ("/leads/new") already satisfies -- so a refused save
      // would verify.
      observation: { url_matches: "/leads/\\d+" },
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

  it("reports a pre-rendered toast that was empty until the action filled it", async () => {
    // The Gmail-style case (PL-018): the page keeps its toast element in the markup and only
    // writes the text after the action. An empty container is not stale evidence — its text
    // arriving is what the window must report.
    document.body.innerHTML =
      "<button id='save'>Save</button>" +
      "<div class='toast' role='status' id='toast' hidden></div>";
    const recorded = harness();
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));

    const toast = document.getElementById("toast")!;
    toast.textContent = "Sent — Undo";
    toast.hidden = false;
    await flushMutations();
    await vi.advanceTimersByTimeAsync(1500);

    expect(recorded.events.map((event) => event.kind)).toEqual(["click", "observation"]);
    expect(recorded.events[1]!.observation).toEqual({ text_visible: "Sent — Undo" });
  });

  it("reports a hidden pre-rendered status whose text was already there and is now revealed", async () => {
    // The corpus widgets' shape: `<div role="status" hidden>Saved</div>` in the markup, revealed
    // by the save. Hidden text is not showing yet, so its reveal is the evidence.
    document.body.innerHTML =
      "<button id='save'>Save</button>" +
      "<div role='status' id='status' hidden>Saved</div>";
    const recorded = harness();
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));

    document.getElementById("status")!.hidden = false;
    await flushMutations();
    await vi.advanceTimersByTimeAsync(1500);

    expect(recorded.events.map((event) => event.kind)).toEqual(["click", "observation"]);
    expect(recorded.events[1]!.observation).toEqual({ text_visible: "Saved" });
  });

  it("reports a status revealed inside an open shadow root", async () => {
    // The corpus's shadow form: the Save reveals `<div role="status" hidden>Saved</div>`
    // inside an open shadow root, where neither a document-level query nor a
    // document-level MutationObserver can see it.
    document.body.innerHTML = "<button id='save'>Save</button><div id='host'></div>";
    const root = document.getElementById("host")!.attachShadow({ mode: "open" });
    root.innerHTML = "<div role='status' id='status' hidden>Saved</div>";
    const recorded = harness();
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));

    (root.getElementById("status") as HTMLElement).hidden = false;
    await flushMutations();
    await vi.advanceTimersByTimeAsync(1500);

    expect(recorded.events.map((event) => event.kind)).toEqual(["click", "observation"]);
    expect(recorded.events[1]!.observation).toEqual({ text_visible: "Saved" });
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

/**
 * PL-073: a toast built from several elements records its message, not every child's text glued
 * together.
 *
 * OrangeHRM's `oxd-toast` is a title ("Success") and a message ("Successfully Saved") in two
 * paragraphs. Their `textContent` runs together -- "SuccessSuccessfully Saved" -- and no page ever
 * shows that string, so the commit check the compiler derives could never pass. Many apps' toasts
 * (Vue, MUI, Ant, Chakra) are built the same way.
 */
describe("a confirmation's message, not its glued-together text", () => {
  /** OrangeHRM 5.7's `oxd-toast`, as the app renders it after a save. */
  const ORANGEHRM_TOAST = `
    <div class="oxd-toast oxd-toast--success" role="status">
      <div class="oxd-toast-container--content">
        <p class="oxd-text oxd-text--toast-title">Success</p>
        <p class="oxd-text oxd-text--toast-message">Successfully Saved</p>
      </div>
    </div>`;

  /** Odoo 18's notification: a title, and the sentence under it. */
  const ODOO_NOTIFICATION = `
    <div class="o_notification d-flex" role="status">
      <div class="o_notification_content">
        <div class="o_notification_title">Saved</div>
        <div class="o_notification_body">The lead was created successfully</div>
      </div>
    </div>`;

  it("records OrangeHRM's toast as its message", async () => {
    document.body.innerHTML = "<button id='save'>Save</button><div id='host'></div>";
    const recorded = harness();
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));

    document.getElementById("host")!.innerHTML = ORANGEHRM_TOAST;
    await flushMutations();
    await vi.advanceTimersByTimeAsync(1500);

    expect(recorded.events[1]!.observation).toEqual({ text_visible: "Successfully Saved" });
  });

  it("records Odoo's notification as its body", async () => {
    document.body.innerHTML = "<button id='save'>Save</button><div id='host'></div>";
    const recorded = harness();
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));

    document.getElementById("host")!.innerHTML = ODOO_NOTIFICATION;
    await flushMutations();
    await vi.advanceTimersByTimeAsync(1500);

    expect(recorded.events[1]!.observation).toEqual({
      text_visible: "The lead was created successfully",
    });
  });

  it("leaves a toast that is one piece of text exactly as it was", async () => {
    document.body.innerHTML = "<button id='save'>Save</button><div id='host'></div>";
    const recorded = harness();
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));

    alertInto("host", "Lead created");
    await flushMutations();
    await vi.advanceTimersByTimeAsync(1500);

    expect(recorded.events[1]!.observation).toEqual({ text_visible: "Lead created" });
  });

  it("takes the longest piece when none of the words is a confirmation word", async () => {
    document.body.innerHTML = "<button id='save'>Save</button><div id='host'></div>";
    const recorded = harness();
    document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));

    document.getElementById("host")!.innerHTML =
      "<div role='status'><p>Notice</p><p>All the changes were applied.</p></div>";
    await flushMutations();
    await vi.advanceTimersByTimeAsync(1500);

    expect(recorded.events[1]!.observation).toEqual({
      text_visible: "All the changes were applied.",
    });
  });
});
