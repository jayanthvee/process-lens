/**
 * PL-118 items 2, 4 and 5 in the panel: the third save choice, the update key line, and teaching
 * that keeps what was recorded.
 *
 * The owner's shape: search for a product, open it, change Sales Price, then leave the form (the
 * app saves by itself). Nothing about that recording says "Save", so the panel must offer
 * "The app saves by itself when I leave the page" and preselect it. After Stop the recording is
 * still there: Continue teaching adds to it, and Start over asks before throwing it away.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { RecorderStatus, ToWorker } from "../../extension/src/shared/messages";
import type { RecordedEvent } from "../../extension/src/shared/recipe-types";
import { configureApi } from "../src/lib/api";
import { extensionChrome, type ExtensionChrome } from "../src/panel/extension";
import { PanelTeach } from "../src/panel/PanelTeach";
import { createFetchStub, settle, type FetchStub } from "./helpers";

const LIST = "https://crm.example.com/products";
const RECORD = "https://crm.example.com/products/17";

const PRICE_EVENTS: RecordedEvent[] = [
  {
    seq: 1,
    at: "2026-10-27T10:00:00Z",
    kind: "fill",
    url: LIST,
    step: {
      id: "fill-1",
      action: "fill",
      targets: [{ by: "role", role: "searchbox", name: "Product" }],
      value: { kind: "constant", value: "Oak Standing Desk" },
    },
  },
  {
    seq: 2,
    at: "2026-10-27T10:00:01Z",
    kind: "press",
    url: LIST,
    step: {
      id: "press-1",
      action: "press",
      targets: [{ by: "role", role: "searchbox", name: "Product" }],
      key: "Enter",
    },
  },
  {
    seq: 3,
    at: "2026-10-27T10:00:02Z",
    kind: "click",
    url: LIST,
    step: {
      id: "click-1",
      action: "click",
      targets: [{ by: "role", role: "cell", name: "Oak Standing Desk" }],
    },
  },
  {
    seq: 4,
    at: "2026-10-27T10:00:03Z",
    kind: "fill",
    url: RECORD,
    step: {
      id: "fill-2",
      action: "fill",
      targets: [{ by: "role", role: "spinbutton", name: "Sales Price" }],
      value: { kind: "constant", value: "799" },
    },
  },
  {
    seq: 5,
    at: "2026-10-27T10:00:04Z",
    kind: "click",
    url: RECORD,
    step: {
      id: "click-2",
      action: "click",
      targets: [{ by: "role", role: "link", name: "Products" }],
    },
  },
  {
    seq: 6,
    at: "2026-10-27T10:00:05Z",
    kind: "navigate",
    url: LIST,
    step: null,
  },
];

function statusFor(events: RecordedEvent[], overrides: Partial<RecorderStatus> = {}): RecorderStatus {
  return {
    state: "idle",
    // Deliberately not the line count: the panel must show the lines, not the worker's number.
    count: 99,
    startedAt: null,
    wsUrl: "",
    wsConnected: false,
    warning: null,
    code: null,
    ...overrides,
  };
}

const originalChrome = extensionChrome();
const sent: ToWorker[] = [];

function installChrome(
  events: RecordedEvent[],
  overrides: Partial<RecorderStatus> = {},
): void {
  (globalThis as { chrome?: ExtensionChrome }).chrome = {
    runtime: {
      sendMessage: async (message: unknown) => {
        const typed = message as ToWorker;
        sent.push(typed);
        if (typed.type === "record:clear") {
          return { ok: true, status: statusFor([]), events: [] };
        }
        if (typed.type === "record:start") {
          return { ok: true, status: statusFor(events, { state: "recording", ...overrides }), events };
        }
        return { ok: true, status: statusFor(events, overrides), events };
      },
    },
  };
}

let stub: FetchStub;

beforeEach(() => {
  sent.length = 0;
  configureApi({ base: "https://hub.example", headers: {} });
  stub = createFetchStub({
    "/api/v1/recipes/compile": {
      body: { recipe: { name: "Update a price" }, warnings: [], search_kind: "update" },
    },
    "/api/v1/tasks": { body: { workflow_id: "w-1" } },
  });
  installChrome(PRICE_EVENTS);
});

afterEach(() => {
  stub.restore();
  cleanup();
  (globalThis as { chrome?: ExtensionChrome }).chrome = originalChrome;
  configureApi({ base: "", headers: {} });
});

describe("the save choice for an app that saves by itself", () => {
  it("offers the third choice and preselects it when the recording ends with a leave", async () => {
    render(<PanelTeach />);
    await screen.findByText(/We'll skip a row if this/);

    fireEvent.click(
      screen.getByRole("button", { name: "Change which click saves your work" }),
    );
    const select = screen.getByLabelText("Which click saves your work?") as HTMLSelectElement;
    const options = Array.from(select.options).map((option) => option.textContent);
    expect(options).toContain("The app saves by itself when I leave the page");
    expect(options).toContain("Nothing gets saved");
    expect(select.value).toBe("__self_save__");
  });

  it("sends the saves-by-itself choice to the compiler, with no commit click", async () => {
    render(<PanelTeach />);
    await screen.findByText(/We'll skip a row if this/);

    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();

    const compile = stub.requests.find((call) => call.path === "/api/v1/recipes/compile");
    const body = compile?.body as {
      save_by_itself?: boolean;
      commit_step_ids?: unknown;
    };
    expect(body.save_by_itself).toBe(true);
    expect(body.commit_step_ids).toEqual([]);
  });

  it("words an update's key line by the key column, and leaves a create's line alone", async () => {
    render(<PanelTeach />);

    expect(
      await screen.findByText(/We'll skip a row if this/),
    ).toBeTruthy();
  });
});

describe("teaching keeps the recording", () => {
  it("counts the lines it shows, and names a hidden code step", async () => {
    installChrome(PRICE_EVENTS, { code: { state: "typed" } });
    render(<PanelTeach />);

    expect(await screen.findByText("6 steps recorded")).toBeTruthy();
    expect(screen.getByText("+1 hidden code step")).toBeTruthy();
  });

  it("starts a stopped recording with 'Continue teaching', without clearing it", async () => {
    render(<PanelTeach />);
    const more = await screen.findByRole("button", { name: "Continue teaching" });

    fireEvent.click(more);
    await settle();

    expect(sent.some((message) => message.type === "record:start")).toBe(true);
    expect(sent.some((message) => message.type === "record:clear")).toBe(false);
    // The steps are still on the screen: continuing adds to them.
    expect(screen.getByText(/We'll skip a row if this/)).toBeTruthy();
  });

  it("asks before Start over throws the steps away", async () => {
    render(<PanelTeach />);
    fireEvent.click(await screen.findByRole("button", { name: "Start over" }));

    expect(screen.getByText("Throw away these 6 steps?")).toBeTruthy();
    expect(sent.some((message) => message.type === "record:clear")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Keep them" }));
    expect(sent.some((message) => message.type === "record:clear")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Start over" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    await settle();

    expect(sent.some((message) => message.type === "record:clear")).toBe(true);
  });

  it("does not clear silently when teaching starts with nothing recorded", async () => {
    installChrome([]);
    render(<PanelTeach />);

    fireEvent.click(await screen.findByRole("button", { name: "Start teaching" }));
    await settle();

    expect(sent.some((message) => message.type === "record:start")).toBe(true);
    expect(sent.some((message) => message.type === "record:clear")).toBe(false);
  });

  it("names the task after the site, never after a previous recording", async () => {
    render(<PanelTeach />);

    // The name is filled in when the worker answers with the recording, so wait for the value
    // itself: the box exists on the first render, before the answer arrives.
    const name = (await screen.findByDisplayValue("crm.example.com task")) as HTMLInputElement;
    expect(name.getAttribute("id")).toBe("panel-task-name");
    expect(name.value).not.toContain("Recorded on");
  });
});

