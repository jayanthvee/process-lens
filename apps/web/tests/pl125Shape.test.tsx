/**
 * PL-125 in the panel: after Stop a task with a shape is three plain questions, not a list of
 * recorded steps to tidy. Saving sends the whole recording with `use_shape`, and the server picks
 * the steps. A name typed before switching panel tabs is still there afterwards.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { RecorderStatus, ToWorker } from "../../extension/src/shared/messages";
import type { RecordedEvent } from "../../extension/src/shared/recipe-types";
import { configureApi } from "../src/lib/api";
import { extensionChrome, type ExtensionChrome } from "../src/panel/extension";
import { PanelTeach } from "../src/panel/PanelTeach";
import { createFetchStub, settle, type FetchStub } from "./helpers";

const LIST = "http://localhost:8069/odoo/products";
const ITEM = "http://localhost:8069/odoo/products/1";

const EVENTS: RecordedEvent[] = [
  {
    seq: 1,
    at: "2026-10-29T05:36:00Z",
    kind: "click",
    url: LIST,
    step: {
      id: "click-1",
      action: "click",
      name: "Click Oak Standing Desk [PL-DESK-1001]",
      targets: [{ by: "role", role: "generic", name: "Oak Standing Desk [PL-DESK-1001]" }],
    },
  },
  {
    seq: 2,
    at: "2026-10-29T05:36:01Z",
    kind: "fill",
    url: ITEM,
    step: {
      id: "fill-1",
      action: "fill",
      name: "Fill Sales Price",
      targets: [{ by: "label", text: "Sales Price" }],
      value: { kind: "constant", value: "2000" },
    },
    keystrokes: 4,
  },
  {
    seq: 3,
    at: "2026-10-29T05:36:02Z",
    kind: "click",
    url: ITEM,
    step: {
      id: "click-2",
      action: "click",
      name: "Click 18.00On Hand18.00Forecasted0",
      targets: [{ by: "role", role: "generic", name: "18.00On Hand18.00Forecasted0" }],
    },
  },
];

const UPDATE_SHAPE = {
  kind: "update",
  problem: null,
  opened: true,
  item: { text: "Oak Standing Desk", label: "product", column: "product" },
  changes: [{ label: "Sales Price", column: "sales_price", value: "2000", was: "3200.00" }],
  save: "leave",
  save_label: "Click button",
};

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
        return { ok: true, status: statusFor(events, state), events };
      },
    },
  };
}

let stub: FetchStub;

function stubShape(shape: unknown): void {
  stub = createFetchStub({
    "/api/v1/recordings/shape": { body: shape },
    "/api/v1/recipes/compile": {
      body: { recipe: { name: "Price update" }, warnings: [], search_kind: "update" },
    },
    "/api/v1/tasks": { body: { workflow_id: "w-1" } },
  });
}

beforeEach(() => {
  sent.length = 0;
  window.sessionStorage.clear();
  configureApi({ base: "https://hub.example", headers: {} });
});

afterEach(() => {
  stub.restore();
  cleanup();
  (globalThis as { chrome?: ExtensionChrome }).chrome = originalChrome;
  configureApi({ base: "", headers: {} });
});

describe("PL-125: the three questions after Stop", () => {
  it("asks which item, what changes and how it is saved, without the recorded steps", async () => {
    stubShape(UPDATE_SHAPE);
    installChrome(EVENTS, "recording");
    render(<PanelTeach />);
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await settle();

    expect(screen.getByText("Which item?")).toBeTruthy();
    expect(screen.getByText(/You opened/).textContent).toContain("Oak Standing Desk");
    expect(screen.getByText("What changes?")).toBeTruthy();
    expect(screen.getByText(/you typed “2000”/)).toBeTruthy();
    expect(screen.getByText("How is it saved?")).toBeTruthy();
    // The stray click is not something the person has to remove: it isn't shown at all.
    expect(screen.queryByText(/Forecasted/)).toBeNull();
    expect(screen.queryByText(/Which click saves your work/)).toBeNull();
    expect(stub.calls).not.toContain("/api/v1/recordings/clean");
  });

  it("saves the whole recording with use_shape and the save answer", async () => {
    stubShape(UPDATE_SHAPE);
    installChrome(EVENTS, "recording");
    render(<PanelTeach />);
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await settle();
    fireEvent.change(screen.getByLabelText("What should this task be called?"), {
      target: { value: "Price update" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();

    const compile = stub.requests.find((call) => call.path === "/api/v1/recipes/compile");
    const body = compile?.body as {
      recording?: { seq: number }[];
      use_shape?: boolean;
      save?: string;
    };
    expect(body.use_shape).toBe(true);
    expect(body.save).toBe("leave");
    expect(body.recording?.map((event) => event.seq)).toEqual([1, 2, 3]);
  });

  it("says what is missing and won't save a task that changes nothing", async () => {
    stubShape({
      ...UPDATE_SHAPE,
      kind: null,
      problem:
        "This task doesn't change anything. Change the value you want each row to set, then Stop again.",
      changes: [],
    });
    installChrome(EVENTS, "recording");
    render(<PanelTeach />);
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await settle();

    expect(screen.getByText(/This task doesn't change anything/)).toBeTruthy();
    const save = screen.getByRole("button", { name: "Save as task" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
  });

  it("keeps a typed name when the panel switches tabs and comes back", async () => {
    stubShape(UPDATE_SHAPE);
    installChrome(EVENTS, "idle");
    const first = render(<PanelTeach />);
    await settle();
    fireEvent.change(screen.getByLabelText("What should this task be called?"), {
      target: { value: "28th sales test" },
    });
    first.unmount();

    render(<PanelTeach />);
    await settle();

    const name = screen.getByLabelText("What should this task be called?") as HTMLInputElement;
    expect(name.value).toBe("28th sales test");
    // The stopped recording's questions are read again, not lost.
    expect(screen.getByText("Which item?")).toBeTruthy();
  });
});
