/**
 * PL-123 item 1 and item 5, in the panel: Stop asks teaching to clean the recording, hidden steps
 * can be shown and brought back, the save line waits for Stop, and the name box starts empty or as
 * "<site> task".
 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { RecorderStatus, ToWorker } from "../../extension/src/shared/messages";
import type { RecordedEvent } from "../../extension/src/shared/recipe-types";
import { configureApi } from "../src/lib/api";
import { extensionChrome, type ExtensionChrome } from "../src/panel/extension";
import { PanelTeach } from "../src/panel/PanelTeach";
import { createFetchStub, settle, type FetchStub } from "./helpers";

const LIST = "https://crm.example.com/products";
const RECORD = "https://crm.example.com/products/17";

const EVENTS: RecordedEvent[] = [
  {
    seq: 1,
    at: "2026-10-28T10:00:00Z",
    kind: "fill",
    url: RECORD,
    step: {
      id: "fill-1",
      action: "fill",
      name: "Fill Sales Price",
      targets: [{ by: "role", role: "textbox", name: "Sales Price" }],
      value: { kind: "constant", value: "799" },
    },
  },
  {
    seq: 2,
    at: "2026-10-28T10:00:01Z",
    kind: "fill",
    url: RECORD,
    step: {
      id: "fill-page",
      action: "fill",
      name: "Fill Cost",
      targets: [{ by: "role", role: "textbox", name: "Cost" }],
      value: { kind: "constant", value: "455" },
    },
    page_set: true,
    keystrokes: 0,
  },
  {
    seq: 3,
    at: "2026-10-28T10:00:02Z",
    kind: "click",
    url: RECORD,
    step: {
      id: "click-1",
      action: "click",
      name: "Click All Apps",
      targets: [{ by: "role", role: "link", name: "All Apps" }],
    },
  },
];

function statusFor(events: RecordedEvent[], state: "idle" | "recording"): RecorderStatus {
  return {
    state,
    count: events.length,
    startedAt: null,
    wsUrl: "",
    wsConnected: false,
    warning: null,
  };
}

const originalChrome = extensionChrome();
const sent: ToWorker[] = [];

function installChrome(events: RecordedEvent[], state: "idle" | "recording"): void {
  (globalThis as { chrome?: ExtensionChrome }).chrome = {
    runtime: {
      sendMessage: async (message: unknown) => {
        const typed = message as ToWorker;
        sent.push(typed);
        if (typed.type === "record:stop") {
          return { ok: true, status: statusFor(events, "idle"), events };
        }
        if (typed.type === "record:clear") {
          return { ok: true, status: statusFor([], "idle"), events: [] };
        }
        return { ok: true, status: statusFor(events, state), events };
      },
    },
  };
}

let stub: FetchStub;

beforeEach(() => {
  sent.length = 0;
  configureApi({ base: "https://hub.example", headers: {} });
  stub = createFetchStub({
    "/api/v1/recordings/clean": {
      body: {
        available: true,
        steps: [
          { id: "fill-1", keep: true, intent: "set Sales Price to the row's New Price" },
          { id: "fill-page", keep: false, intent: "" },
          { id: "click-1", keep: false, intent: "" },
        ],
      },
    },
    "/api/v1/recipes/compile": {
      body: { recipe: { name: "Recorded task" }, warnings: [], search_kind: null },
    },
    "/api/v1/tasks": { body: { workflow_id: "w-1" } },
  });
});

afterEach(() => {
  stub.restore();
  cleanup();
  (globalThis as { chrome?: ExtensionChrome }).chrome = originalChrome;
  configureApi({ base: "", headers: {} });
});

describe("the panel's cleaning", () => {
  it("does not say a click saves the work while the recording is still running", async () => {
    installChrome(EVENTS.slice(0, 2), "recording");
    render(<PanelTeach />);

    await screen.findByText(/step/);
    expect(screen.queryByText(/click saves your work/i)).toBeNull();
  });

  it("starts the name empty or as the site's task, never an old task's name", async () => {
    installChrome(EVENTS.slice(0, 1), "idle");
    render(<PanelTeach />);

    const name = (await screen.findByLabelText("What should this task be called?")) as HTMLInputElement;
    expect(name.value === "" || name.value.endsWith(" task")).toBe(true);
    expect(name.value).not.toBe("New lead");
  });

  it("hides the steps the page did by itself, and can bring one back", async () => {
    // PL-125: no model is asked at Stop. The page's own value is hidden by its flag; the stray
    // click is the person's and stays (a task with a shape never shows it; see pl125Shape).
    installChrome(EVENTS, "recording");
    render(<PanelTeach />);
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await settle();

    expect(stub.calls).not.toContain("/api/v1/recordings/clean");
    expect(screen.getByText(/Hidden: 1 step the page did by itself/)).toBeTruthy();
    expect(screen.queryByText(/Typed the cost/i)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Show the steps the page did by itself/ }));
    const hidden = screen.getByText(/Typed the cost/i).closest("li") as HTMLElement;
    fireEvent.click(within(hidden).getByRole("button", { name: "Bring back" }));
    await settle();

    expect(screen.queryByText(/Hidden:/)).toBeNull();
    expect(screen.getByText(/Typed the cost/i)).toBeTruthy();
  });

  it("saves without the page's own value and sends no model intents", async () => {
    installChrome(EVENTS, "recording");
    render(<PanelTeach />);
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();

    const compile = stub.requests.find((call) => call.path === "/api/v1/recipes/compile");
    const body = compile?.body as {
      recording?: { seq: number }[];
      intents?: Record<string, string>;
    };
    expect(body.recording?.map((event) => event.seq)).toEqual([1, 3]);
    expect(body.intents ?? {}).toEqual({});
  });
});
