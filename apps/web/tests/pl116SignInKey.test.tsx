/**
 * PL-116 item 3: a sign-in task's save screen does not talk about a duplicate-protection key.
 *
 * A sign-in runs with one email; "We'll use Email so nothing gets added twice" describes a
 * spreadsheet task and is noise here. The key is still worked out and sent exactly as before --
 * only the line, its Change button and the note are hidden.
 */
import { cleanup, render, screen } from "@testing-library/react";
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

function statusFor(events: RecordedEvent[], signIn: boolean): RecorderStatus {
  return {
    state: "idle",
    count: events.length,
    startedAt: null,
    wsUrl: "",
    wsConnected: false,
    warning: null,
    signIn,
  };
}

const originalChrome = extensionChrome();
let stub: FetchStub;

function installChrome(events: RecordedEvent[], signIn: boolean): void {
  (globalThis as { chrome?: ExtensionChrome }).chrome = {
    runtime: {
      sendMessage: async (message: unknown) => {
        const typed = message as ToWorker;
        const state = typed.type === "record:start" ? "recording" : "idle";
        return { ok: true, status: { ...statusFor(events, signIn), state }, events };
      },
    },
    tabs: { query: async () => [{ id: 7 }] },
  } as unknown as ExtensionChrome;
}

beforeEach(() => {
  configureApi({ base: "https://hub.example", headers: {} });
  stub = createFetchStub({});
});

afterEach(() => {
  stub.restore();
  cleanup();
  (globalThis as { chrome?: ExtensionChrome }).chrome = originalChrome;
  configureApi({ base: "", headers: {} });
});

async function saveScreen(events: RecordedEvent[], signIn: boolean): Promise<void> {
  installChrome(events, signIn);
  render(<PanelTeach />);
  // The save decision is on screen once the recording has been read back. A sign-in says it saves
  // nothing (PL-117); any other task says which click saves.
  await screen.findByText(signIn ? "A sign-in saves nothing." : /click saves your work/i);
  await settle();
}

describe("a sign-in's save screen has no add-twice line (PL-116)", () => {
  it("does not show the key line, its Change button, or the note", async () => {
    await saveScreen(SIGN_IN, true);

    expect(screen.queryByText(/nothing gets added twice/i)).toBeNull();
    expect(screen.queryByText(/We'll use/i)).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Change the value that is different for every row" }),
    ).toBeNull();
    expect(screen.queryByText("Which value is different for every row?")).toBeNull();
  });

  it("still shows the key line for a recording that is not a sign-in", async () => {
    await saveScreen(SIGN_IN, false);

    await screen.findByText("Email", { selector: "b" });
    expect(screen.getByText(/so nothing gets added twice/i)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Change the value that is different for every row" }),
    ).toBeTruthy();
  });

  it("still asks which value is different when a sign-in typed no identifiable one", async () => {
    // A username sign-in: "jsmith" is not an email, an id or a person's name, so no key can be
    // picked for the person. Hiding the question too would leave Save disabled with no way out.
    const username: RecordedEvent[] = [
      {
        seq: 1,
        at: "2026-10-27T10:00:00Z",
        kind: "fill",
        url: LOGIN,
        step: {
          id: "fill-1",
          action: "fill",
          targets: [{ by: "role", role: "textbox", name: "Username" }],
          value: { kind: "constant", value: "jsmith" },
        },
      },
      SIGN_IN[1] as RecordedEvent,
    ];

    await saveScreen(username, true);

    expect(
      screen.getByRole("combobox", { name: "Which value is different for every row?" }),
    ).toBeTruthy();
    // The note that says why it is asked is still the one thing a sign-in does not need.
    expect(screen.queryByText(/nothing gets added twice/i)).toBeNull();
  });
});
