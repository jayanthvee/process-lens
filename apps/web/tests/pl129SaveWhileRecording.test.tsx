/**
 * PL-129 in the panel: the owner's price task of 2026-10-29 was saved while the panel still said
 * Recording. While recording the panel shows the old step list, and Save as task built the task
 * from it: a search, a skip when the product is found, and the card click as the save. Every row was
 * then skipped as "already there". A task is saved only after Stop, from the three questions.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type {
  RecorderStatus,
  ToWorker,
} from "../../extension/src/shared/messages";
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
    at: "2026-10-29T07:24:00Z",
    kind: "click",
    url: LIST,
    step: {
      id: "click-1",
      action: "click",
      name: "Click $ 100.00",
      targets: [{ by: "role", role: "generic", name: "$ 100.00" }],
    },
  },
  {
    seq: 2,
    at: "2026-10-29T07:24:05Z",
    kind: "fill",
    url: ITEM,
    step: {
      id: "fill-1",
      action: "fill",
      name: "Fill Sales Price",
      targets: [{ by: "label", text: "Sales Price" }],
      value: { kind: "constant", value: "3000" },
    },
    keystrokes: 4,
  },
];

const UPDATE_SHAPE = {
  kind: "update",
  problem: null,
  opened: true,
  item: { text: "Oak Standing Desk", label: "product", column: "product" },
  changes: [
    {
      label: "Sales Price",
      column: "sales_price",
      value: "3000",
      was: "100.00",
    },
  ],
  save: "leave",
  save_label: null,
};

function statusFor(state: "idle" | "recording"): RecorderStatus {
  return {
    state,
    count: EVENTS.length,
    startedAt: null,
    wsUrl: "",
    wsConnected: false,
    warning: null,
  };
}

const originalChrome = extensionChrome();
const sent: ToWorker[] = [];

function installChrome(state: "idle" | "recording"): void {
  (globalThis as { chrome?: ExtensionChrome }).chrome = {
    runtime: {
      sendMessage: async (message: unknown) => {
        const typed = message as ToWorker;
        sent.push(typed);
        if (typed.type === "record:stop") {
          return { ok: true, status: statusFor("idle"), events: EVENTS };
        }
        return { ok: true, status: statusFor(state), events: EVENTS };
      },
    },
  };
}

let stub: FetchStub;

beforeEach(() => {
  sent.length = 0;
  window.sessionStorage.clear();
  configureApi({ base: "https://hub.example", headers: {} });
  stub = createFetchStub({
    "/api/v1/recordings/shape": { body: UPDATE_SHAPE },
    "/api/v1/recipes/compile": {
      body: {
        recipe: { name: "Price update" },
        warnings: [],
        search_kind: "update",
      },
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

describe("PL-129: no task is saved while the panel is recording", () => {
  it("offers no Save as task while recording, only Stop", async () => {
    installChrome("recording");
    render(<PanelTeach />);
    await settle();

    expect(screen.getByText("Recording")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Save as task" })).toBeNull();
    expect(
      screen.queryByLabelText("What should this task be called?"),
    ).toBeNull();
    expect(screen.getByText(/Press Stop when you're done/)).toBeTruthy();
    expect(stub.calls).not.toContain("/api/v1/recipes/compile");
  });

  it("saves from the three questions once Stop is pressed", async () => {
    installChrome("recording");
    render(<PanelTeach />);
    await settle();

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await settle();
    fireEvent.change(
      screen.getByLabelText("What should this task be called?"),
      {
        target: { value: "Price update" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();

    const compile = stub.requests.find(
      (call) => call.path === "/api/v1/recipes/compile",
    );
    expect((compile?.body as { use_shape?: boolean }).use_shape).toBe(true);
  });
});
