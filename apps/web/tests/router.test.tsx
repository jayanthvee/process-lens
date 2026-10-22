/** The router: every sidebar route lands on a real page, and no page is ever blank. */

import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createFetchStub, renderApp, settle, type FetchStub } from "./helpers";

/** Every path the sidebar can reach. */
const PATHS = [
  "/",
  "/needs-help",
  "/tasks",
  "/tasks/w-1",
  "/tasks/w-1/run",
  "/runs",
  "/runs/run-1",
  "/teach",
  "/spreadsheets",
  "/documents",
  "/saved-answers",
  "/apps",
  "/settings",
  "/help",
];

/** The pages this ticket does not build: each shows the designed empty state. */
const COMING_SOON = [
  "/documents",
  "/saved-answers",
  "/help",
];

let stub: FetchStub;

beforeEach(() => {
  stub = createFetchStub({
    "/api/v1/needs-help": { body: [] },
    "/api/v1/executor/session": { body: { attached: false } },
    "/api/v1/tasks": { body: [] },
    "/api/v1/apps": { body: [] },
    "/api/v1/spreadsheets": { body: [] },
  });
});

afterEach(() => {
  stub.restore();
  cleanup();
});

describe("the router", () => {
  it("renders every sidebar path with a page title and at most one primary action", async () => {
    for (const path of PATHS) {
      const view = renderApp(path);
      await settle(1);

      expect(screen.getByRole("heading", { level: 2 }).textContent, path).toBeTruthy();
      expect(document.querySelectorAll(".btn.primary").length, path).toBeLessThanOrEqual(1);
      expect(screen.getByRole("main").textContent ?? "", path).not.toBe("");

      view.unmount();
    }
  });

  it("gives a detail route its own page rather than the list beside it", async () => {
    const view = renderApp("/runs/run-1");
    await settle(1);

    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Run");
    view.unmount();
  });

  it("shows a designed empty state on every page this ticket does not build", async () => {
    for (const path of COMING_SOON) {
      const view = renderApp(path);
      await settle(1);

      expect(screen.getAllByText("Coming soon").length, path).toBeGreaterThan(0);
      expect(screen.getByText(/will appear here|coming next/i), path).toBeTruthy();

      view.unmount();
    }
  });
});
