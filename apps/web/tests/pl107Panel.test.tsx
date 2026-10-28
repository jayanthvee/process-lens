/**
 * PL-107: the side panel's Teach page, at what the owner's first real-site teach got wrong.
 *
 * - There was no way to start over, and Start teaching added to the old steps.
 * - The last click was always taken as the save, even a click in Gmail; a sign-in saves nothing
 *   (PL-117 now says so on screen and sends no commit choice at all).
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
const FORM = "https://app.example.test/leads/new";

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

function statusFor(
  events: RecordedEvent[],
  state: "idle" | "recording" = "idle",
  signIn = true,
): RecorderStatus {
  return {
    state,
    count: events.length,
    startedAt: null,
    wsUrl: "",
    wsConnected: false,
    warning: null,
    // The shared fixture is a sign-in (PL-107/PL-112): PL-117 says it saves nothing on screen and
    // sends no commit choice. The save-choice tests below need an ordinary recording, so they
    // install ``LEAD`` with ``signIn: false``.
    signIn,
  };
}

/** An ordinary teach: type an email on a form, then press Save. */
const LEAD: RecordedEvent[] = [
  {
    seq: 1,
    at: "2026-10-26T10:00:00Z",
    kind: "fill",
    url: FORM,
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
    url: FORM,
    step: {
      id: "save-1",
      action: "click",
      targets: [{ by: "role", role: "button", name: "Save" }],
    },
  },
];

const originalChrome = extensionChrome();
const sent: ToWorker[] = [];

function installChrome(events: RecordedEvent[], signIn = true): void {
  let current = events;
  (globalThis as { chrome?: ExtensionChrome }).chrome = {
    runtime: {
      sendMessage: async (message: unknown) => {
        const typed = message as ToWorker;
        sent.push(typed);
        if (typed.type === "record:clear") current = [];
        const state = typed.type === "record:start" ? "recording" : "idle";
        return { ok: true, status: statusFor(current, state, signIn), events: current };
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
    // PL-117: a sign-in says it saves nothing, so that is the line a stopped sign-in shows.
    await screen.findByText("A sign-in saves nothing.");

    // PL-118 item 5: Start over asks on this screen before it throws the steps away.
    fireEvent.click(screen.getByRole("button", { name: "Start over" }));
    expect(screen.getByText("Throw away these 3 steps?")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    await settle();

    expect(sent.some((message) => message.type === "record:clear")).toBe(true);
    expect(screen.queryByText("A sign-in saves nothing.")).toBeNull();
  });

  it("Continue teaching adds to the stopped recording, without clearing it", async () => {
    render(<PanelTeach />);
    await screen.findByText("A sign-in saves nothing.");

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
    installChrome(LEAD, false);
    render(<PanelTeach />);
    await screen.findByText(/click saves your work/i);
    fireEvent.click(screen.getByRole("button", { name: "Change which click saves your work" }));
    await settle();

    const choice = screen.getByRole("combobox", { name: "Which click saves your work?" });
    const options = Array.from((choice as HTMLSelectElement).options).map((o) => o.text);
    // A recording with its own Save click offers that click; PL-118's "saves by itself" is only
    // offered when no click reads as a save.
    expect(options).toEqual(["Clicked Save", "Nothing gets saved"]);
  });

  it("'Nothing gets saved' compiles with no save step (a sign-in, a lookup)", async () => {
    installChrome(LEAD, false);
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
    installChrome(LEAD, false);
    render(<PanelTeach />);
    await screen.findByText(/click saves your work/i);
    fireEvent.click(screen.getByRole("button", { name: "Change which click saves your work" }));
    await settle();

    const choice = screen.getByRole("combobox", { name: "Which click saves your work?" });
    fireEvent.change(choice, { target: { value: "save-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();

    expect(compileBody().commit_step_ids).toEqual(["save-1"]);
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
            problems: ["step save-1 (commit) has no assert_after"],
          },
        },
      },
    });
    installChrome(LEAD, false);
    render(<PanelTeach />);
    await screen.findByText(/click saves your work/i);

    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();

    expect(screen.queryByText("Could not save this as a task.")).toBeNull();
    // The sentence names the click and what to do, on this same screen.
    expect(screen.getByText(/couldn't see anything change after “Save”/i)).toBeTruthy();
    expect(screen.getByText(/Which click saves your work/i)).toBeTruthy();
  });
});
