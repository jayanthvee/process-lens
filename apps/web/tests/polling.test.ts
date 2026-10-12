/** Polling: every 2 seconds while the run is open, and not one tick after it is finished. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mountApp } from "../src/app";
import * as logic from "../src/logic";
import { OPERATOR_ID, createFetchStub, sampleRecord, sampleRunDetail, settle } from "./helpers";
import type { RunDetail } from "../src/types";

describe("the interval", () => {
  it("is two seconds, and stops as soon as the run stops being open", () => {
    const ticks: number[] = [];
    let status = "running";
    const poller = logic.createPoller({
      onTick: () => {
        ticks.push(Date.now());
      },
      shouldContinue: () => logic.isOpenStatus(status),
    });
    expect(logic.POLL_INTERVAL_MS).toBe(2000);
    expect(poller.isRunning()).toBe(false);
    poller.start();
    expect(poller.isRunning()).toBe(true);
    poller.start(); // idempotent: one interval, not two
    poller.stop();
    expect(poller.isRunning()).toBe(false);
    expect(ticks).toEqual([]);
    expect(status).toBe("running");
  });

  it("knows which statuses are open", () => {
    for (const open of ["queued", "running", "paused"]) {
      expect(logic.shouldPoll(open)).toBe(true);
    }
    for (const finished of ["completed", "cancelled", "failed"]) {
      expect(logic.shouldPoll(finished)).toBe(false);
    }
    expect(logic.shouldPoll(null)).toBe(false);
  });
});

describe("the page's poll", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("ticks every two seconds while the run is open and stops once it is finished", async () => {
    let run: RunDetail = sampleRunDetail({ status: "running" });
    let detailCalls = 0;
    const { fetchImpl } = createFetchStub((call) => {
      if (call.path === "/api/v1/operator") return { status: 200, body: { operator_id: OPERATOR_ID } };
      if (call.path === "/api/v1/runs/run-1") {
        detailCalls += 1;
        return { status: 200, body: run };
      }
      if (call.path === "/api/v1/runs/run-1/records") {
        return { status: 200, body: [sampleRecord({ state: "verified", commit_attempted: false })] };
      }
      if (call.path === "/api/v1/runs/run-1/approvals") return { status: 200, body: [] };
      return { status: 404, body: { detail: `unexpected ${call.path}` } };
    });

    const root = document.createElement("div");
    document.body.appendChild(root);
    const app = mountApp(root, { fetchImpl });
    await app.openRun("run-1");
    expect(app.status().polling).toBe(true);
    const afterFirstLoad = detailCalls;

    await vi.advanceTimersByTimeAsync(2000);
    await settle();
    expect(app.status().polling).toBe(true);
    expect(detailCalls).toBeGreaterThan(afterFirstLoad);

    const afterOneTick = detailCalls;
    await vi.advanceTimersByTimeAsync(2000);
    await settle();
    expect(detailCalls).toBeGreaterThan(afterOneTick);

    // The API now reports the run as finished: the next refresh must stop the poll for good.
    run = sampleRunDetail({ status: "completed" });
    await vi.advanceTimersByTimeAsync(2000);
    await settle();
    expect(app.status().runStatus).toBe("completed");
    expect(app.status().polling).toBe(false);

    const afterFinish = detailCalls;
    await vi.advanceTimersByTimeAsync(10_000);
    await settle();
    expect(detailCalls).toBe(afterFinish);
    expect(app.status().polling).toBe(false);
    app.destroy();
  });

  it("does not poll a run that is already finished", async () => {
    const { fetchImpl } = createFetchStub((call) => {
      if (call.path === "/api/v1/operator") return { status: 200, body: { operator_id: OPERATOR_ID } };
      if (call.path === "/api/v1/runs/run-1") {
        return { status: 200, body: sampleRunDetail({ status: "failed" }) };
      }
      if (call.path === "/api/v1/runs/run-1/records") return { status: 200, body: [] };
      if (call.path === "/api/v1/runs/run-1/approvals") return { status: 200, body: [] };
      return { status: 404, body: { detail: "unexpected" } };
    });

    const root = document.createElement("div");
    document.body.appendChild(root);
    const app = mountApp(root, { fetchImpl });
    await app.openRun("run-1");
    expect(app.status().polling).toBe(false);
    app.destroy();
  });
});
