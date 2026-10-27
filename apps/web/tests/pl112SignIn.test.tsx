/**
 * PL-112 item 4: a sign-in task runs from an email pick, not a spreadsheet.
 *
 * The task is marked while teaching, its Tasks row offers the taught email first, Run sends that one
 * value through the existing run-once path, and a failed code read is said in plain words on the
 * same row.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";

import type { RecorderStatus } from "../../extension/src/shared/messages";
import type { RecordedEvent } from "../../extension/src/shared/recipe-types";
import { configureApi } from "../src/lib/api";
import { extensionChrome, type ExtensionChrome } from "../src/panel/extension";
import { PanelTasks } from "../src/panel/PanelTasks";
import { PanelTeach } from "../src/panel/PanelTeach";
import type { TaskRow } from "../src/types";
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
    signIn: true,
  };
}

const originalChrome = extensionChrome();
const storage: Record<string, unknown> = {};

function installChrome(events: RecordedEvent[]): void {
  (globalThis as { chrome?: ExtensionChrome }).chrome = {
    runtime: {
      sendMessage: async (message: unknown) => {
        const state =
          (message as { type?: string }).type === "record:start" ? "recording" : "idle";
        return { ok: true, status: { ...statusFor(events), state }, events };
      },
    },
    tabs: { query: async () => [{ id: 7 }] },
    storage: {
      local: {
        get: async (keys: string[]) => {
          const answer: Record<string, unknown> = {};
          for (const key of keys) if (key in storage) answer[key] = storage[key];
          return answer;
        },
        set: async (items: Record<string, unknown>) => {
          Object.assign(storage, items);
        },
      },
    },
  } as unknown as ExtensionChrome;
}

function signInTask(overrides: Partial<TaskRow> = {}): TaskRow {
  return {
    workflow_id: "w-signin",
    recipe_version_id: "rv-1",
    name: "Sign in to the app",
    app: "app.example.test",
    last_run: null,
    inputs: ["email"],
    sign_in: true,
    sign_in_column: "email",
    sign_in_email: "a@example.com",
    ...overrides,
  };
}

function renderTasks(): void {
  render(
    <MemoryRouter initialEntries={["/tasks"]}>
      <PanelTasks />
    </MemoryRouter>,
  );
}

let stub: FetchStub;

beforeEach(() => {
  for (const key of Object.keys(storage)) delete storage[key];
  configureApi({ base: "https://hub.example", headers: {} });
  stub = createFetchStub({
    "/api/v1/tasks": (call) =>
      call.method === "POST"
        ? { body: { workflow_id: "w-signin" } }
        : { body: [signInTask()] },
    "/api/v1/tasks/w-signin/run-once": {
      status: 201,
      body: { run_id: "run-9", status: "queued" },
    },
    "/api/v1/runs/run-9/code-from-email": {
      body: { entered: true, resumed: true },
    },
    "/api/v1/recipes/compile": {
      body: { recipe: { name: "Sign in" }, warnings: [], search_kind: null },
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

describe("teaching a sign-in", () => {
  it("marks the saved task and remembers the taught email in this Chrome", async () => {
    render(<PanelTeach />);
    await screen.findByText(/click saves your work/i);

    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();

    const saved = stub.requests.find(
      (call) => call.path === "/api/v1/tasks" && call.method === "POST",
    );
    expect(saved?.body).toMatchObject({
      sign_in: true,
      sign_in_column: "email",
    });
    expect(JSON.stringify(storage)).toContain("a@example.com");
  });
});

describe("a sign-in task's row", () => {
  it("offers Run with the taught email first and no spreadsheet button", async () => {
    renderTasks();
    await settle();

    expect(screen.getByRole("button", { name: "Run" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Run with a spreadsheet" })).toBeNull();
    const choice = screen.getByRole("combobox", { name: /email/i }) as HTMLSelectElement;
    expect(Array.from(choice.options).map((option) => option.text)).toEqual([
      "a@example.com",
      "Another email…",
    ]);
  });

  it("Run sends one row with the picked email in the taught column", async () => {
    renderTasks();
    await settle();

    const choice = screen.getByRole("combobox", { name: /email/i });
    fireEvent.change(choice, { target: { value: "__another__" } });
    fireEvent.change(screen.getByRole("textbox", { name: /email/i }), {
      target: { value: "picked@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await settle();

    const started = stub.requests.find((call) => call.path === "/api/v1/tasks/w-signin/run-once");
    expect(started?.method).toBe("POST");
    expect(started?.body).toMatchObject({ values: { email: "picked@example.com" } });
    expect(JSON.stringify(storage)).toContain("picked@example.com");
  });

  it("says no code arrived at the picked address", async () => {
    stub.restore();
    stub = createFetchStub({
      "/api/v1/tasks": {
        body: [
          signInTask({
            last_run: {
              run_id: "run-9",
              status: "paused",
              started_at: "2026-10-26T10:00:00Z",
              counts: {},
              pause_reason: "code_required",
              pause_detail: "none",
            },
          }),
        ],
      },
    });
    renderTasks();
    await settle();

    expect(
      screen.getByText(
        "No code arrived at a@example.com. Check that this inbox is signed in to Gmail in this Chrome, then press Try again.",
      ),
    ).toBeTruthy();
  });

  it("says two codes could not be told apart, in PL-102's words", async () => {
    stub.restore();
    stub = createFetchStub({
      "/api/v1/tasks": {
        body: [
          signInTask({
            last_run: {
              run_id: "run-9",
              status: "paused",
              started_at: "2026-10-26T10:00:00Z",
              counts: {},
              pause_reason: "code_required",
              pause_detail: "ambiguous",
            },
          }),
        ],
      },
    });
    renderTasks();
    await settle();

    expect(
      screen.getByText(
        "Two codes arrived and ProcessLens can't tell which one is this site's. Type the right one here.",
      ),
    ).toBeTruthy();
  });

  it("offers the Gmail read when the person has not allowed it yet", async () => {
    stub.restore();
    stub = createFetchStub({
      "/api/v1/tasks": {
        body: [
          signInTask({
            last_run: {
              run_id: "run-9",
              status: "paused",
              started_at: "2026-10-26T10:00:00Z",
              counts: {},
              pause_reason: "code_required",
              pause_detail: "not_allowed",
            },
          }),
        ],
      },
      "/api/v1/runs/run-9/code-from-email": { body: { entered: true, resumed: true } },
    });
    renderTasks();
    await settle();

    expect(screen.getByText(/ProcessLens can read it from your Gmail/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Read it from my Gmail" }));
    await settle();

    const read = stub.requests.find((call) => call.path === "/api/v1/runs/run-9/code-from-email");
    expect(read?.body).toEqual({ remember: true });
  });
});
