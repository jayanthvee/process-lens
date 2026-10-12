// A full-page save (PL-016 requirement 3): the click that submits it happens on one page and
// the confirmation is rendered on the next, so the page that arrives reports it and the worker
// links it back to the click. Before this, an observation never survived the navigation and the
// compiled recipe had no `assert_after` for the save.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ARRIVAL_OBSERVATION_WINDOW_MS, attachRecorder } from "../src/content/recorder";
import { RecorderSession } from "../src/background/recorder-session";
import type { PendingEvent } from "../src/shared/events";
import { resetStepIds } from "../src/shared/events";
import type { Condition } from "../src/shared/recipe-types";

const FROM = "http://127.0.0.1:8123/new-lead.html";
const TO = "http://127.0.0.1:8123/saved.html";

interface Page {
  detach: () => void;
  events: PendingEvent[];
}

/** The page that sends: it records actions and tells the worker when it is leaving. */
function sendingPage(worker: RecorderSession): Page {
  const events: PendingEvent[] = [];
  const detach = attachRecorder(document, {
    send: (event) => {
      events.push(event);
      worker.append(event);
    },
    isActive: () => true,
    url: () => FROM,
    now: () => "2026-10-12T00:00:00.000Z",
    onLeaving: () => {
      worker.noteLeaving();
    },
  });
  return { detach, events };
}

/** The page that arrives: it reports the confirmation the server rendered with it. */
function arrivingPage(
  stepId: string,
  worker: RecorderSession,
  reports: Condition[],
): Page {
  const events: PendingEvent[] = [];
  const detach = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => true,
    url: () => TO,
    now: () => "2026-10-12T00:00:00.000Z",
    arrival: { stepId },
    onArrivalObservation: (id, observation) => {
      reports.push(observation);
      worker.attachObservation(id, observation);
    },
  });
  return { detach, events };
}

/** Let MutationObserver microtasks run before the window's timers are advanced. */
function flushMutations(): Promise<void> {
  return new Promise((done) => {
    done();
  });
}

/** The click on Save, and the document unloads into the next page. */
function saveAndLeave(worker: RecorderSession): void {
  document.body.innerHTML = "<button id='save' type='submit'>Save</button>";
  const sending = sendingPage(worker);
  document.getElementById("save")!.dispatchEvent(new Event("click", { bubbles: true }));
  expect(sending.events.map((event) => event.kind)).toEqual(["click"]);
  window.dispatchEvent(new Event("pagehide"));
  sending.detach();
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("an observation survives a full-page save", () => {
  it("links the arrived page's status text to the click that navigated", async () => {
    const worker = new RecorderSession({ now: () => 1_000 });
    saveAndLeave(worker);

    const target = worker.pendingTarget();
    expect(target).toBe("click-1");

    // The page that arrives: the server rendered the confirmation with it.
    document.body.innerHTML = "<div role='status'>Lead created</div>";
    const reports: Condition[] = [];
    const arriving = arrivingPage(target!, worker, reports);
    await vi.advanceTimersByTimeAsync(ARRIVAL_OBSERVATION_WINDOW_MS);
    arriving.detach();

    expect(reports).toEqual([{ text_visible: "Lead created" }]);
    const click = worker.events.find((event) => event.step?.id === "click-1")!;
    expect(click.observation).toEqual({ text_visible: "Lead created" });
    expect(worker.events.filter((event) => event.observation).length).toBe(1);
  });

  it("reports a toast that appears after the page has loaded", async () => {
    const worker = new RecorderSession({ now: () => 1_000 });
    saveAndLeave(worker);

    document.body.innerHTML = "<div id='host'></div>";
    const reports: Condition[] = [];
    const arriving = arrivingPage(worker.pendingTarget()!, worker, reports);

    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = "Lead created";
    document.getElementById("host")!.appendChild(toast);
    await flushMutations();
    await vi.advanceTimersByTimeAsync(10);

    expect(reports).toEqual([{ text_visible: "Lead created" }]);
    arriving.detach();
  });

  it("says nothing when the arrived page shows no confirmation", async () => {
    const worker = new RecorderSession({ now: () => 1_000 });
    saveAndLeave(worker);

    document.body.innerHTML = "<h1>Saved leads</h1>";
    const reports: Condition[] = [];
    const arriving = arrivingPage(worker.pendingTarget()!, worker, reports);
    await vi.advanceTimersByTimeAsync(ARRIVAL_OBSERVATION_WINDOW_MS * 2);
    arriving.detach();

    expect(reports).toEqual([]);
    const click = worker.events.find((event) => event.step?.id === "click-1")!;
    expect(click.observation).toBeUndefined();
  });

  it("does not report a confirmation that appears after the three-second window", async () => {
    const worker = new RecorderSession({ now: () => 1_000 });
    saveAndLeave(worker);

    document.body.innerHTML = "<div id='host'></div>";
    const reports: Condition[] = [];
    const arriving = arrivingPage(worker.pendingTarget()!, worker, reports);

    await vi.advanceTimersByTimeAsync(ARRIVAL_OBSERVATION_WINDOW_MS + 1);
    const late = document.createElement("div");
    late.setAttribute("role", "status");
    late.textContent = "Lead created";
    document.getElementById("host")!.appendChild(late);
    await flushMutations();
    await vi.advanceTimersByTimeAsync(1000);

    expect(reports).toEqual([]);
    arriving.detach();
  });

  it("never reports while recording is off", async () => {
    const reports: Condition[] = [];
    document.body.innerHTML = "<div role='status'>Lead created</div>";
    const detach = attachRecorder(document, {
      send: () => undefined,
      isActive: () => false,
      url: () => TO,
      arrival: { stepId: "click-1" },
      onArrivalObservation: (_id, observation) => {
        reports.push(observation);
      },
    });

    await vi.advanceTimersByTimeAsync(ARRIVAL_OBSERVATION_WINDOW_MS);
    detach();

    expect(reports).toEqual([]);
  });
});
