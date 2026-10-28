/**
 * PL-127 in the panel: the owner's Events task was saved through the old step list because the
 * shape was not read at Stop. That list replayed a card click and treated New as the save. A
 * recording is now always asked for its shape before it is saved, and an unread shape is never
 * saved any other way.
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

function stubShapeFailing(times: number): void {
  let failures = 0;
  stub = createFetchStub({
    "/api/v1/recordings/shape": () => {
      failures += 1;
      return failures <= times ? { status: 503, body: { detail: "busy" } } : { body: UPDATE_SHAPE };
    },
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

describe("PL-127: an unread shape is read again, never skipped", () => {
  it("hides the old step list and says so when the shape can't be read at Stop", async () => {
    stubShapeFailing(1);
    installChrome(EVENTS, "recording");
    render(<PanelTeach />);
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await settle();

    expect(screen.getByText(/couldn't read what you taught/)).toBeTruthy();
    expect(screen.queryByText(/Which click saves your work/)).toBeNull();
    expect(screen.queryByText(/Forecasted/)).toBeNull();
  });

  it("reads the shape again on Save and builds the task from it", async () => {
    stubShapeFailing(1);
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
    const body = compile?.body as { use_shape?: boolean; save?: string };
    expect(body.use_shape).toBe(true);
    expect(body.save).toBe("leave");
  });

  it("saves nothing while the shape still can't be read", async () => {
    stubShapeFailing(5);
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

    expect(stub.calls).not.toContain("/api/v1/recipes/compile");
    expect(screen.getAllByText(/couldn't read what you taught/).length).toBeGreaterThan(0);
  });
});
