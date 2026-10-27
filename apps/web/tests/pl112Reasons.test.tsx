/**
 * PL-112 item 3: every finished row's reason is plain, including a row stored with the old words.
 */

import { cleanup, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { plainReason } from "../src/lib/words";
import type { RecordRow, RunDetail as RunPayload } from "../src/types";
import { createFetchStub, renderApp, settle, type FetchStub } from "./helpers";

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  cleanup();
});

const DETAIL: RunPayload = {
  id: "run-1",
  status: "completed",
  pause_reason: null,
  counts: { total: 1, verified: 1, skipped: 0, parked: 0, failed: 0, in_progress: 0 },
  open_approval: null,
  started_at: "2026-10-26T10:00:00Z",
  finished_at: "2026-10-26T10:00:10Z",
};

function row(reason: string): RecordRow {
  return {
    id: "rr-1",
    record_key: "ACME-1",
    state: "verified",
    reason,
    attempts: 1,
    max_attempts: 3,
    commit_attempted: true,
    destination_ref: "crm.example.com/leads/9",
  };
}

describe("plainReason for a finished row", () => {
  it("turns the old verified wording into 'Added and checked'", () => {
    expect(plainReason("assertion passed")).toBe("Added and checked.");
    expect(plainReason("the destination has this commit")).toBe("Added and checked.");
  });

  it("turns a read-only row's old wording into 'Done'", () => {
    expect(plainReason("steps completed")).toBe("Done.");
  });

  it("gives the other engine words a plain sentence too", () => {
    expect(plainReason("commit write-ahead")).toBe("Saving it now.");
    expect(plainReason("precheck passed")).toBe("Checked the row.");
    expect(plainReason("the assertion did not pass")).toBe(
      "The page didn't show it saved, so ProcessLens checked the site.",
    );
    expect(plainReason("no dispatch proof")).toBe(
      "ProcessLens couldn't tell whether the click was sent.",
    );
  });
});

describe("a row already stored with the old text", () => {
  async function renderRow(reason: string): Promise<string> {
    stub = createFetchStub({
      "/api/v1/needs-help": { body: [] },
      "/api/v1/executor/session": { body: { attached: true } },
      "/api/v1/operator": { body: { operator_id: "0f8fad5b-d9cb-469f-a165-70867728950e" } },
      "/api/v1/runs/run-1/records": { body: [row(reason)] },
      "/api/v1/runs/run-1": { body: DETAIL },
    });
    const view = renderApp("/runs/run-1");
    await settle();
    return view.container.textContent ?? "";
  }

  it("shows 'Added and checked', never 'Assertion passed'", async () => {
    const text = await renderRow("assertion passed");

    expect(text).toContain("Added and checked.");
    expect(text.toLowerCase()).not.toContain("assertion passed");
  });

  it("shows 'Done' for a read-only row stored as 'steps completed'", async () => {
    const text = await renderRow("steps completed");

    expect(text).toContain("Done.");
    expect(text.toLowerCase()).not.toContain("steps completed");
  });
});
