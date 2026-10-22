/**
 * PL-080: the Settings page's one section, "Daily limits per site".
 *
 * Each row is a site and a number of rows per day; add, edit and remove are local until Save, which
 * replaces the whole set in one request. Only an admin may change them: everyone else sees the
 * same rows read-only, and the run page's "Change the limit" link is shown to an admin only.
 */

import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { SiteLimits } from "../src/types";
import { createFetchStub, renderApp, settle, type FetchStub, type RecordedRequest } from "./helpers";

const SHELL = {
  "/api/v1/needs-help": { body: [] },
  "/api/v1/executor/session": { body: { attached: false } },
  "/api/v1/operator": { body: { operator_id: "0f8fad5b-d9cb-469f-a165-70867728950e" } },
};

const LIMITS: SiteLimits = {
  tenant_id: "tenant-1",
  sites: [{ origin: "https://www.linkedin.com", limit: 25 }],
  can_change: true,
};

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function harness(limits: SiteLimits = LIMITS): void {
  stub = createFetchStub({
    ...SHELL,
    "/api/v1/settings/site-limits": (call: RecordedRequest) =>
      call.method === "PUT" ? { body: { ...limits, sites: (call.body as { sites: unknown }).sites } } : { body: limits },
  });
}

function saves(): RecordedRequest[] {
  return (stub as FetchStub).requests.filter(
    (call) => call.method === "PUT" && call.path === "/api/v1/settings/site-limits",
  );
}

function siteInputs(): HTMLInputElement[] {
  return screen.getAllByLabelText("Site") as HTMLInputElement[];
}

function limitInputs(): HTMLInputElement[] {
  return screen.getAllByLabelText("Rows per day") as HTMLInputElement[];
}

describe("the Settings page", () => {
  it("is a real page with one section of daily limits", async () => {
    harness();
    renderApp("/settings");
    await settle();

    // The shell's own header names the page; the page itself is the one section, and its heading
    // is what a person reads.
    expect(screen.getByRole("heading", { name: "Daily limits per site" })).toBeTruthy();
    expect(screen.queryByText("This screen will be built in a later release.")).toBeNull();
    expect(siteInputs()[0]?.value).toBe("https://www.linkedin.com");
    expect(limitInputs()[0]?.value).toBe("25");
    // The section is reachable by its own anchor, which is where the run page links.
    expect(document.querySelector("#daily-limits")).not.toBeNull();
  });

  it("edits a limit and saves the whole set", async () => {
    harness();
    renderApp("/settings");
    await settle();

    fireEvent.change(limitInputs()[0] as HTMLInputElement, { target: { value: "40" } });
    await settle();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    });
    await settle();

    expect(saves()).toHaveLength(1);
    expect(saves()[0]?.body).toEqual({
      sites: [{ origin: "https://www.linkedin.com", limit: 40 }],
    });
    expect(screen.getByText("Saved.")).toBeTruthy();
  });

  it("adds a site", async () => {
    harness();
    renderApp("/settings");
    await settle();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Add a site" }));
    });
    await settle();

    fireEvent.change(siteInputs()[1] as HTMLInputElement, {
      target: { value: "https://salesforce.example" },
    });
    fireEvent.change(limitInputs()[1] as HTMLInputElement, { target: { value: "100" } });
    await settle();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    });
    await settle();

    expect(saves()[0]?.body).toEqual({
      sites: [
        { origin: "https://www.linkedin.com", limit: 25 },
        { origin: "https://salesforce.example", limit: 100 },
      ],
    });
  });

  it("removes a site", async () => {
    harness();
    renderApp("/settings");
    await settle();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    });
    await settle();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    });
    await settle();

    expect(saves()[0]?.body).toEqual({ sites: [] });
  });

  it("says in plain words what the API refused", async () => {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/settings/site-limits": (call: RecordedRequest) =>
        call.method === "PUT"
          ? { status: 422, body: { detail: "site 1: an origin must be http://host or https://host" } }
          : { body: LIMITS },
    });
    renderApp("/settings");
    await settle();

    fireEvent.change(limitInputs()[0] as HTMLInputElement, { target: { value: "40" } });
    await settle();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    });
    await settle();

    expect(
      screen.getByText("site 1: an origin must be http://host or https://host"),
    ).toBeTruthy();
  });

  it("shows the limits read-only to someone who is not an admin", async () => {
    harness({ ...LIMITS, can_change: false });
    renderApp("/settings");
    await settle();

    expect(screen.getByText("https://www.linkedin.com")).toBeTruthy();
    expect(screen.getByText("25 rows a day")).toBeTruthy();
    expect(screen.getByText("Only an admin can change the daily limits.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add a site" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
    expect(screen.queryByLabelText("Rows per day")).toBeNull();
  });
});

describe("the run page's Change the limit link", () => {
  function runWithCap(canChange: boolean): void {
    stub = createFetchStub({
      ...SHELL,
      "/api/v1/runs/run-1/records": { body: [] },
      "/api/v1/runs/run-1": {
        body: {
          id: "run-1",
          status: "paused",
          pause_reason: "site_daily_cap",
          counts: { total: 3 },
          open_approval: null,
          started_at: null,
          finished_at: null,
          site_cap: {
            origin: "https://www.linkedin.com",
            limit: 25,
            used: 25,
            can_change: canChange,
          },
        },
      },
    });
  }

  it("goes to the Settings section for an admin", async () => {
    runWithCap(true);
    renderApp("/runs/run-1");
    await settle();

    const link = screen.getByRole("link", { name: "Change the limit" });
    expect(link.getAttribute("href")).toBe("/settings#daily-limits");
  });

  it("is not shown to everyone else", async () => {
    runWithCap(false);
    renderApp("/runs/run-1");
    await settle();

    expect(screen.queryByRole("link", { name: "Change the limit" })).toBeNull();
  });
});
