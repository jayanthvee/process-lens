/**
 * PL-107: the side panel's Teach page, at what the owner's first real-site teach got wrong.
 *
 * - There was no way to start over, and Start teaching added to the old steps.
 * - The last click was always taken as the save, even a click in Gmail; a sign-in saves nothing.
 * - A refused save said only "Could not save this as a task."
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
      id: "click-1",
      action: "click",
      targets: [{ by: "role", role: "button", name: "Continue" }],
    },
  },
  {
    seq: 3,
    at: "2026-10-26T10:00:20Z",
    kind: "click",
    url: "https://app.example.test/home",
    step: {
      id: "click-2",
      action: "click",
      targets: [{ by: "role", role: "button", name: "View results" }],
    },
  },
];

function statusFor(events: RecordedEvent[], state: "idle" | "recording" = "idle"): RecorderStatus {
  return {
    state,
    count: events.length,
    startedAt: null,
    wsUrl: "",
    wsConnected: false,
    warning: null,
    // The fixture is a sign-in (PL-107/PL-112): it saves nothing, so PL-118's self-save choice is
    // not offered and the last click keeps its old default.
    signIn: true,
  };
}

const originalChrome = extensionChrome();
const sent: ToWorker[] = [];

function installChrome(events: RecordedEvent[]): void {
  let current = events;
  (globalThis as { chrome?: ExtensionChrome }).chrome = {
    runtime: {
      sendMessage: async (message: unknown) => {
        const typed = message as ToWorker;
        sent.push(typed);
        if (typed.type === "record:clear") current = [];
        const state = typed.type === "record:start" ? "recording" : "idle";
        return { ok: true, status: statusFor(current, state), events: current };
      },
    },
    tabs: { query: async () => [{ id: 7 }] },
  } as unknown as ExtensionChrome;
}

let stub: FetchStub;

beforeEach(() => {
  sent.length = 0;
  configureApi({ base: "https://hub.example", headers: {} });
  stub = createFetchStub({
    "/api/v1/recipes/compile": {
      body: { recipe: { name: "Sign in" }, warnings: [], search_kind: null },
    },
    "/api/v1/tasks": { body: { workflow_id: "w-1" } },
  });
  installChrome(SIGN_IN);
});

afterEach(() => {
  stub.restore();
  cleanup();
  (globalThis as { chrome?: ExtensionChrome }).chrome = originalChrome;
  configureApi({ base: "", headers: {} });
});

describe("starting over", () => {
  it("always offers Start over once something is recorded, and it clears the recording", async () => {
    render(<PanelTeach />);
    await screen.findByText(/click saves your work/i);

    // PL-118 item 5: Start over asks on this screen before it throws the steps away.
    fireEvent.click(screen.getByRole("button", { name: "Start over" }));
    expect(screen.getByText("Throw away these 3 steps?")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    await settle();

    expect(sent.some((message) => message.type === "record:clear")).toBe(true);
    expect(screen.queryByText(/click saves your work/i)).toBeNull();
  });

  it("Continue teaching adds to the stopped recording, without clearing it", async () => {
    render(<PanelTeach />);
    await screen.findByText(/click saves your work/i);

    fireEvent.click(screen.getByRole("button", { name: "Continue teaching" }));
    await settle();

    const kinds = sent.map((message) => message.type);
    expect(kinds).toContain("record:start");
    // PL-118 item 5: Start teaching/Continue teaching no longer clears silently.
    expect(kinds).not.toContain("record:clear");
  });
});

describe("which click saves", () => {
  function compileBody(): { commit_step_ids?: unknown } {
    const compile = stub.requests.find((call) => call.path === "/api/v1/recipes/compile");
    return (compile?.body ?? {}) as { commit_step_ids?: unknown };
  }

  it("asks, with the recorded clicks and 'Nothing gets saved' as the choices", async () => {
    render(<PanelTeach />);
    await screen.findByText(/click saves your work/i);
    fireEvent.click(screen.getByRole("button", { name: "Change which click saves your work" }));
    await settle();

    const choice = screen.getByRole("combobox", { name: "Which click saves your work?" });
    const options = Array.from((choice as HTMLSelectElement).options).map((o) => o.text);
    expect(options).toEqual(["Clicked Continue", "Clicked View results", "Nothing gets saved"]);
  });

  it("'Nothing gets saved' compiles with no save step (a sign-in, a lookup)", async () => {
    render(<PanelTeach />);
    await screen.findByText(/click saves your work/i);
    fireEvent.click(screen.getByRole("button", { name: "Change which click saves your work" }));
    await settle();

    const choice = screen.getByRole("combobox", { name: "Which click saves your work?" });
    fireEvent.change(choice, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();

    expect(compileBody().commit_step_ids).toEqual([]);
  });

  it("a chosen click is the one sent", async () => {
    render(<PanelTeach />);
    await screen.findByText(/click saves your work/i);
    fireEvent.click(screen.getByRole("button", { name: "Change which click saves your work" }));
    await settle();

    const choice = screen.getByRole("combobox", { name: "Which click saves your work?" });
    fireEvent.change(choice, { target: { value: "click-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();

    expect(compileBody().commit_step_ids).toEqual(["click-1"]);
  });
});

describe("a refused save says why", () => {
  it("never shows the bare 'Could not save this as a task.'", async () => {
    stub.restore();
    stub = createFetchStub({
      "/api/v1/recipes/compile": {
        status: 422,
        body: {
          detail: {
            message: "a commit step must carry an assertion, and none could be inferred",
            problems: ["step click-2 (commit) has no assert_after"],
          },
        },
      },
    });
    render(<PanelTeach />);
    await screen.findByText(/click saves your work/i);

    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();

    expect(screen.queryByText("Could not save this as a task.")).toBeNull();
    // The sentence names the click and what to do, on this same screen.
    expect(
      screen.getByText(/couldn't see anything change after “View results”/i),
    ).toBeTruthy();
    expect(screen.getByText(/Which click saves your work/i)).toBeTruthy();
  });
});
