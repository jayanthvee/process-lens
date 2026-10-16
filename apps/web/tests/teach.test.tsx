/**
 * "Teach a new task": the three plain steps, and the page noticing the task the moment it arrives.
 */

import { act, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { POLL_INTERVAL_MS } from "../src/lib/useJson";
import type { TaskRow } from "../src/types";
import { callsTo, createFetchStub, renderApp, settle, type FetchStub } from "./helpers";

/* Every path the Shell asks for as well, so a page test never hits the real API. */
const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: false } },
};

function task(overrides: Partial<TaskRow> = {}): TaskRow {
  return {
    workflow_id: "wf-1",
    recipe_version_id: "rv-1",
    name: "Add inquiries",
    app: "crm.example.com",
    last_run: null,
    ...overrides,
  };
}

/** Wait for the page to settle, whether the timers are real or faked. */
async function pump(): Promise<void> {
  if (vi.isFakeTimers()) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    return;
  }
  await settle(4);
}

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  vi.useRealTimers();
});

describe("the teach page", () => {
  it("says what to do in three plain steps, and asks for nothing else", async () => {
    stub = createFetchStub({ ...SHELL, "/api/v1/tasks": { body: [] } });
    renderApp("/teach");
    await pump();

    expect(screen.getByText("Open the app you want to automate in Chrome.")).toBeTruthy();
    expect(screen.getByText("Press Start teaching in the ProcessLens extension.")).toBeTruthy();
    expect(screen.getByText("Do one row by hand, exactly as you would normally.")).toBeTruthy();
    // Nothing to press: teaching happens in the extension, and this page only watches for it.
    expect(document.querySelectorAll(".btn.primary")).toHaveLength(0);
    expect(screen.getByText(/Nothing yet/)).toBeTruthy();
  });

  it("says the tasks that were already there are not new", async () => {
    stub = createFetchStub({ ...SHELL, "/api/v1/tasks": { body: [task()] } });
    renderApp("/teach");
    await pump();

    expect(screen.queryByText(/Got it/)).toBeNull();
  });

  it("says 'Got it' and links to the task as soon as one appears", async () => {
    vi.useFakeTimers();
    let body: TaskRow[] = [];
    const fetchStub = createFetchStub({ ...SHELL, "/api/v1/tasks": () => ({ body }) });
    stub = fetchStub;

    renderApp("/teach");
    await pump();
    expect(screen.getByText(/Nothing yet/)).toBeTruthy();

    body = [task({ name: "Send invoices", workflow_id: "wf-2" })];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    await pump();

    const link = screen.getByRole("link", { name: "Got it: Send invoices" });
    expect(link.getAttribute("href")).toBe("/tasks/wf-2");
    expect(callsTo(fetchStub, "/api/v1/tasks")).toBeGreaterThan(1);
  });
});
