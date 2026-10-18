/**
 * PL-041 part 3: a task that keeps failing says so, with the fix.
 *
 * A saved task never changes, so one recorded before a fix keeps failing the same way: the owner's
 * "New CRM Lead" looked healthy in the task list while every run parked its rows for one reason.
 * The task list and the top of the run page now say so in plain words, and offer the one action
 * that fixes it -- recording the task again -- without sending the person to hunt for it.
 */

import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { engineWordsIn } from "../src/lib/words";
import type { TaskRow } from "../src/types";
import { createFetchStub, renderApp, settle, type FetchStub } from "./helpers";

/* Every path the Shell asks for as well, so a page test never hits the real API. */
const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: false } },
};

const REASON = "the confirmation was already on the page before saving";

/** The sentence both pages show, with the reason in the words `plainReason` gives it. */
const SENTENCE =
  "This task stopped working the last two times (The confirmation was already on the page " +
  "before saving). Record it again in the extension to fix it.";

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function task(overrides: Partial<TaskRow> = {}): TaskRow {
  return {
    workflow_id: "wf-1",
    recipe_version_id: "rv-1",
    name: "New CRM Lead",
    app: "crm.example.com",
    last_run: null,
    keeps_failing: null,
    ...overrides,
  };
}

function warning(container: HTMLElement): HTMLElement | null {
  return container.querySelector<HTMLElement>(".keeps-failing");
}

describe("a task that stopped working", () => {
  it("says so on its row in the task list, and offers the way to record it again", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/tasks": { body: [task({ keeps_failing: { reason: REASON } })] },
    });
    const view = renderApp("/tasks");
    await settle();

    const block = warning(view.container);
    expect(block, "the task row says the task stopped working").not.toBeNull();
    expect(block?.textContent).toContain(SENTENCE);
    expect(screen.getByRole("link", { name: "How to record it again" }).getAttribute("href")).toBe(
      "/teach",
    );
    expect(engineWordsIn(block?.textContent ?? "")).toEqual([]);
  });

  it("says nothing on a task that is working", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/tasks": { body: [task({ keeps_failing: null })] },
    });
    const view = renderApp("/tasks");
    await settle();

    expect(warning(view.container)).toBeNull();
    expect(view.container.textContent).not.toContain("stopped working");
    expect(screen.queryByRole("link", { name: "How to record it again" })).toBeNull();
  });

  it("says so at the top of the run page, before the person adds a spreadsheet", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/tasks": { body: [task({ keeps_failing: { reason: REASON } })] },
    });
    const view = renderApp("/tasks/wf-1/run");
    await settle();

    const block = warning(view.container);
    expect(block, "the run page says the task stopped working").not.toBeNull();
    expect(block?.textContent).toContain(SENTENCE);
    expect(screen.getByRole("link", { name: "How to record it again" }).getAttribute("href")).toBe(
      "/teach",
    );
    expect(engineWordsIn(block?.textContent ?? "")).toEqual([]);
  });

  it("says nothing at the top of the run page for a task that is working", async () => {
    stub = createFetchStub({ ...SHELL, "/api/v1/tasks": { body: [task()] } });
    const view = renderApp("/tasks/wf-1/run");
    await settle();

    expect(warning(view.container)).toBeNull();
    expect(screen.queryByRole("link", { name: "How to record it again" })).toBeNull();
  });
});
