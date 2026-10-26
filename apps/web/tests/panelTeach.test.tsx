/**
 * PL-105 review: the panel's Save sends the recording's last click as the commit step, exactly as
 * the popup preselected it. With no commit step the save click compiles as a plain click: no
 * write-ahead and no save check, so a crash mid-save can create the record twice.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { configureApi } from "../src/lib/api";
import { PanelTeach } from "../src/panel/PanelTeach";
import { extensionChrome, type ExtensionChrome } from "../src/panel/extension";
import type { RecorderStatus } from "../../extension/src/shared/messages";
import type { RecordedEvent } from "../../extension/src/shared/recipe-types";
import { createFetchStub, settle, type FetchStub } from "./helpers";

const EVENTS: RecordedEvent[] = [
  {
    seq: 1,
    at: "2026-10-26T10:00:00Z",
    kind: "click",
    url: "https://crm.example.com/leads/new",
    step: {
      id: "step-form",
      action: "click",
      targets: [{ by: "role", role: "button", name: "Go to the form" }],
    },
  },
  {
    seq: 2,
    at: "2026-10-26T10:00:02Z",
    kind: "click",
    url: "https://crm.example.com/leads/new",
    step: {
      id: "step-save",
      action: "click",
      targets: [{ by: "role", role: "button", name: "Save" }],
    },
  },
  // The page showed the save's result; it carries no step, so the save click stays the last click.
  {
    seq: 3,
    at: "2026-10-26T10:00:03Z",
    kind: "observation",
    url: "https://crm.example.com/leads/42",
    step: null,
  },
];

const STATUS: RecorderStatus = {
  state: "idle",
  count: EVENTS.length,
  startedAt: null,
  wsUrl: "",
  wsConnected: false,
  warning: null,
};

const originalChrome = extensionChrome();

function installChrome(events: RecordedEvent[]): void {
  (globalThis as { chrome?: ExtensionChrome }).chrome = {
    runtime: {
      // The worker answers "what is recorded" the same way for every recorder message.
      sendMessage: async () => ({ ok: true, status: STATUS, events }),
    },
  };
}

let stub: FetchStub;

beforeEach(() => {
  configureApi({ base: "https://hub.example", headers: {} });
  stub = createFetchStub({
    "/api/v1/recipes/compile": {
      body: { recipe: { name: "Recorded task" }, warnings: [], search_kind: null },
    },
    "/api/v1/tasks": { body: { workflow_id: "w-1" } },
  });
  installChrome(EVENTS);
});

afterEach(() => {
  stub.restore();
  cleanup();
  (globalThis as { chrome?: ExtensionChrome }).chrome = originalChrome;
  configureApi({ base: "", headers: {} });
});

describe("the panel's Save", () => {
  it("sends the recording's last click as the commit step", async () => {
    render(<PanelTeach />);

    await screen.findByText("Clicked Save");
    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();

    const compile = stub.requests.find((call) => call.path === "/api/v1/recipes/compile");
    expect(compile?.method).toBe("POST");
    const body = compile?.body as { commit_step_ids?: unknown };
    expect(body.commit_step_ids).toEqual(["step-save"]);
    // The compile is followed by the save that makes the task real.
    expect(stub.calls).toContain("/api/v1/tasks");
  });
});
