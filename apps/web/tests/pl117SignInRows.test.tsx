/**
 * PL-117: a sign-in row saves nothing, the destination tick names the site, and no raw URL sentence
 * reaches a screen.
 *
 * A sign-in row offers Try again only: there is no destination to check and nothing to dismiss. A
 * normal task's tick names the site it will look in, as PL-115's cards do. Old reasons that still
 * carry the runner's URL wording are shown plainly.
 */

import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { NeedsHelpRow, RecordRow, RunDetail as RunPayload } from "../src/types";
import { createFetchStub, renderApp, settle, type FetchStub, type StubRoutes } from "./helpers";

const OPERATOR_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const SIGN_IN_REASON = "The sign-in didn't finish: the site never got past the code page.";

const SHELL = {
  "/api/v1/operator": { body: { operator_id: OPERATOR_ID } },
  "/api/v1/executor/session": { body: { attached: true } },
};

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  cleanup();
});

function helpRow(overrides: Partial<NeedsHelpRow> = {}): NeedsHelpRow {
  return {
    record_id: "record-1",
    run_id: "run-1",
    task_name: "Add inquiries",
    row: "ACME-1",
    state: "parked",
    reason: "missing email",
    commit_attempted: false,
    app: "https://goodlabs.com",
    sign_in: false,
    ...overrides,
  };
}

function runDetail(overrides: Partial<RunPayload> = {}): RunPayload {
  return {
    id: "run-1",
    status: "completed",
    pause_reason: null,
    counts: { total: 1, verified: 0, skipped: 0, parked: 1, failed: 0, in_progress: 0 },
    open_approval: null,
    started_at: null,
    finished_at: null,
    app: "https://goodlabs.com",
    sign_in: false,
    ...overrides,
  };
}

function record(overrides: Partial<RecordRow> = {}): RecordRow {
  return {
    id: "rr-1",
    record_key: "ravi@example.test",
    state: "parked",
    reason: "missing email",
    attempts: 1,
    max_attempts: 3,
    commit_attempted: false,
    destination_ref: null,
    ...overrides,
  };
}

function help(rows: NeedsHelpRow[], extra: StubRoutes = {}): void {
  stub = createFetchStub({
    ...SHELL,
    "/api/v1/needs-help": { body: rows },
    ...extra,
  });
  renderApp("/needs-help");
}

describe("a sign-in row on Needs your help", () => {
  it("offers Try again only, with no destination tick and no Dismiss advice", async () => {
    help(
      [
        helpRow({
          task_name: "Sign in to Goodlabs",
          sign_in: true,
          commit_attempted: true,
          reason: SIGN_IN_REASON,
        }),
      ],
      {
        "/api/v1/records/record-1/requeue": {
          body: { record_id: "record-1", state: "pending" },
        },
      },
    );
    await settle();

    expect(screen.getByText(SIGN_IN_REASON)).toBeTruthy();
    expect(screen.queryByText(/Dismiss/i)).toBeNull();
    expect(document.querySelector(".destination-check-box")).toBeNull();
    const tryAgain = screen.getByRole("button", { name: "Try again" }) as HTMLButtonElement;
    // An old sign-in recipe can still carry commit_attempted; a sign-in never needs the tick.
    expect(tryAgain.disabled).toBe(false);

    fireEvent.click(tryAgain);
    await settle();
    const sent = stub?.requests.filter(
      (call) => call.method === "POST" && call.path === "/api/v1/records/record-1/requeue",
    );
    // Nothing to check on the destination, so the body carries no destination_checked_by.
    expect(sent).toHaveLength(1);
    expect(sent?.[0]?.body).toEqual({});
  });

  it("drops an older stored reason's Dismiss advice", async () => {
    help([
      helpRow({
        task_name: "Sign in to Goodlabs",
        sign_in: true,
        commit_attempted: true,
        reason: `${SIGN_IN_REASON.slice(0, -1)}; check goodlabs.com: if the row is there, Dismiss it, otherwise Try again`,
      }),
    ]);
    await settle();

    expect(screen.getByText(SIGN_IN_REASON)).toBeTruthy();
    expect(screen.queryByText(/Dismiss it/i)).toBeNull();
    expect(screen.queryByText(/otherwise Try again/i)).toBeNull();
  });
});

describe("the destination tick names the site", () => {
  it("uses the task's site name, as the task cards show it", async () => {
    help([
      helpRow({
        task_name: "Add inquiries",
        app: "https://goodlabs.com",
        commit_attempted: true,
      }),
    ]);
    await settle();

    expect(document.querySelector(".destination-check-label")?.textContent).toBe(
      "I checked goodlabs.com and it isn't there",
    );
    expect(document.querySelector(".destination-check-why")?.textContent).toContain(
      "goodlabs.com",
    );
  });
});

describe("no raw URL sentence", () => {
  it("shows an old URL-matching reason in plain words", async () => {
    help([
      helpRow({
        reason: 'the page never went to a URL matching "/home/" within 8 seconds',
        commit_attempted: false,
      }),
    ]);
    await settle();

    expect(screen.getByText("The page didn't move on within 8 seconds.")).toBeTruthy();
    expect(screen.queryByText(/URL matching/i)).toBeNull();
  });

  it("shows the same plain words on a run's row", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1": { body: runDetail() },
      "/api/v1/runs/run-1/records": {
        body: [
          record({
            reason: 'the page never went to a URL matching "/home/" within 8 seconds',
          }),
        ],
      },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(screen.getByText("The page didn't move on within 8 seconds.")).toBeTruthy();
    expect(screen.queryByText(/URL matching/i)).toBeNull();
  });
});

describe("a sign-in row on a run", () => {
  it("shows no destination tick and no Dismiss button", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1": { body: runDetail({ sign_in: true }) },
      "/api/v1/runs/run-1/records": {
        body: [record({ commit_attempted: true, reason: SIGN_IN_REASON })],
      },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(screen.getByText(SIGN_IN_REASON)).toBeTruthy();
    expect(document.querySelector(".destination-check-box")).toBeNull();
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("names the site in a normal run's tick", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1": { body: runDetail() },
      "/api/v1/runs/run-1/records": {
        body: [record({ commit_attempted: true })],
      },
    });
    renderApp("/runs/run-1");
    await settle();

    expect(document.querySelector(".destination-check-label")?.textContent).toBe(
      "I checked goodlabs.com and it isn't there",
    );
  });
});
