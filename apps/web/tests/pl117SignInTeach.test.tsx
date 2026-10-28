/**
 * PL-117 item 1, teaching screen: a sign-in does not offer "The Continue click saves your work".
 *
 * The panel still tells the compiler this was a sign-in and where it landed. It sends no commit
 * choice, so the task is compiled (and shown) as one that saves nothing.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { RecorderStatus } from "../../extension/src/shared/messages";
import type { RecordedEvent } from "../../extension/src/shared/recipe-types";
import { configureApi } from "../src/lib/api";
import { extensionChrome, type ExtensionChrome } from "../src/panel/extension";
import { PanelTeach } from "../src/panel/PanelTeach";
import { createFetchStub, settle, type FetchStub } from "./helpers";

const LOGIN = "https://app.example.test/login";
const HOME = "https://app.example.test/home";

const RECORDING: RecordedEvent[] = [
  {
    seq: 1,
    at: "2026-10-27T10:00:00Z",
    kind: "fill",
    url: LOGIN,
    step: {
      id: "fill-1",
      action: "fill",
      targets: [{ by: "role", role: "textbox", name: "Email" }],
      value: { kind: "constant", value: "a@example.com" },
    },
  },
  {
    seq: 2,
    at: "2026-10-27T10:00:01Z",
    kind: "click",
    url: LOGIN,
    step: {
      id: "click-2",
      action: "click",
      targets: [{ by: "role", role: "button", name: "Continue" }],
    },
  },
];

function status(): RecorderStatus {
  return {
    state: "idle",
    count: RECORDING.length,
    startedAt: null,
    wsUrl: "",
    wsConnected: false,
    warning: null,
    signIn: true,
    signInCodeUrl: "https://app.example.test/verify",
    signInAfterUrl: HOME,
  };
}

const originalChrome = extensionChrome();
let stub: FetchStub;

beforeEach(() => {
  configureApi({ base: "https://hub.example", headers: {} });
  stub = createFetchStub({
    "/api/v1/recipes/compile": {
      body: { recipe: { name: "Sign in" }, warnings: [], search_kind: null },
    },
    "/api/v1/tasks": { body: { workflow_id: "w-1", recipe_version_id: "rv-1" } },
  });
  (globalThis as { chrome?: ExtensionChrome }).chrome = {
    runtime: {
      sendMessage: async () => ({ ok: true, status: status(), events: RECORDING }),
    },
    tabs: { query: async () => [{ id: 7 }] },
    storage: { local: { get: async () => ({}), set: async () => undefined } },
  } as unknown as ExtensionChrome;
});

afterEach(() => {
  stub.restore();
  cleanup();
  (globalThis as { chrome?: ExtensionChrome }).chrome = originalChrome;
});

describe("teaching a sign-in (PL-117)", () => {
  it("says the sign-in saves nothing and sends no commit choice", async () => {
    render(<PanelTeach />);
    await screen.findByText("A sign-in saves nothing.");
    await settle();

    expect(screen.queryByText(/click saves your work/i)).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Change which click saves your work" }),
    ).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();

    const compiled = stub.requests.find((call) => call.path === "/api/v1/recipes/compile");
    expect(compiled?.body).toMatchObject({
      sign_in: true,
      sign_in_after_path: HOME,
      commit_step_ids: [],
    });
  });
});
