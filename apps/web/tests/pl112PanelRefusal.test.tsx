/**
 * PL-112 item 2: the panel's refusal never contradicts the save choice, and changing the choice
 * clears the old refusal.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { RecorderStatus, ToWorker } from "../../extension/src/shared/messages";
import type { RecordedEvent } from "../../extension/src/shared/recipe-types";
import { configureApi } from "../src/lib/api";
import { extensionChrome, type ExtensionChrome } from "../src/panel/extension";
import { PanelTeach } from "../src/panel/PanelTeach";
import { createFetchStub, settle, type FetchStub } from "./helpers";

const LOGIN = "https://app.example.test/login";

const SIGN_IN: RecordedEvent[] = [
  {
    seq: 1,
    at: "2026-10-26T10:00:00Z",
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
    at: "2026-10-26T10:00:01Z",
    kind: "click",
    url: LOGIN,
    step: {
      id: "click-2",
      action: "click",
      targets: [{ by: "role", role: "button", name: "Continue" }],
    },
  },
];

function statusFor(events: RecordedEvent[]): RecorderStatus {
  return {
    state: "idle",
    count: events.length,
    startedAt: null,
    wsUrl: "",
    wsConnected: false,
    warning: null,
  };
}

const originalChrome = extensionChrome();

function installChrome(events: RecordedEvent[]): void {
  (globalThis as { chrome?: ExtensionChrome }).chrome = {
    runtime: {
      sendMessage: async (message: unknown) => {
        const typed = message as ToWorker;
        const state = typed.type === "record:start" ? "recording" : "idle";
        return { ok: true, status: { ...statusFor(events), state }, events };
      },
    },
    tabs: { query: async () => [{ id: 7 }] },
  } as unknown as ExtensionChrome;
}

let stub: FetchStub;

beforeEach(() => {
  configureApi({ base: "https://hub.example", headers: {} });
  stub = createFetchStub({
    "/api/v1/recipes/compile": {
      status: 422,
      body: {
        detail: {
          message: "a commit step must carry an assertion, and none could be inferred",
          problems: [
            "commit step click-2 has no assert_after: the recording observed no confirmation and "
              + "no navigation, and none was supplied for it",
          ],
        },
      },
    },
  });
  installChrome(SIGN_IN);
});

afterEach(() => {
  stub.restore();
  cleanup();
  (globalThis as { chrome?: ExtensionChrome }).chrome = originalChrome;
  configureApi({ base: "", headers: {} });
});

async function chooseNothing(): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: "Change which click saves your work" }));
  await settle();
  fireEvent.change(screen.getByRole("combobox", { name: "Which click saves your work?" }), {
    target: { value: "" },
  });
}

describe("the save choice and a refusal", () => {
  it("under 'Nothing gets saved' says the recording can't be saved yet, never to choose it", async () => {
    render(<PanelTeach />);
    await screen.findByText(/click saves your work/i);
    await chooseNothing();

    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();

    const refusal = screen.getByText(/This recording can't be saved yet:/i);
    expect(refusal.textContent).not.toMatch(/choose “Nothing gets saved”/i);
    expect(refusal.textContent).not.toMatch(/pick a different answer/i);
  });

  it("clears the old refusal as soon as the save choice changes", async () => {
    render(<PanelTeach />);
    await screen.findByText(/click saves your work/i);

    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();
    expect(screen.getByText(/couldn't see anything change after “Continue”/i)).toBeTruthy();

    await chooseNothing();
    await settle();

    expect(screen.queryByText(/couldn't see anything change after “Continue”/i)).toBeNull();
    expect(screen.queryByText(/This recording can't be saved yet:/i)).toBeNull();
  });
});
