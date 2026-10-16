// The abandon guard (PL-027): a record that leaves the tab without committing must not take its
// half-filled form with it.
//
// The page's behaviour being modelled is Odoo's: it autosaves a dirty form when the document
// unloads, so a record the runner parked would be written by the destination itself, with no
// commit step and no approval. The guard intercepts the tab's requests and refuses everything that
// is not a read.
import { beforeEach, describe, expect, it } from "vitest";
import {
  BLOCKED_ERROR_REASON,
  BLOCKED_LIMIT,
  armAbandonGuard,
  blockedRequests,
  disarmAbandonGuard,
  guardArmed,
  resetAbandonGuard,
} from "../src/executor/abandon-guard";
import { fakeCdp } from "./helpers/fake-cdp";

beforeEach(() => {
  resetAbandonGuard();
});

/** A request as CDP's `Fetch.requestPaused` reports it. */
function paused(requestId: string, method: string, url: string): Record<string, unknown> {
  return { requestId, request: { method, url }, resourceType: "XHR" };
}

describe("the abandon guard", () => {
  it("starts unarmed and arms by enabling Fetch interception", async () => {
    const { cdp, callsTo } = fakeCdp();
    expect(guardArmed()).toBe(false);

    await armAbandonGuard(cdp);

    expect(guardArmed()).toBe(true);
    // A page load is hundreds of requests; pausing the ones that cannot carry a write starved the
    // extension's single worker thread and killed the run's own hub socket.
    const patterns = (
      callsTo("Fetch.enable")[0]?.params as {
        patterns: { resourceType: string; urlPattern: string; requestStage: string }[];
      }
    ).patterns;
    expect(patterns.map((pattern) => pattern.resourceType)).toEqual([
      "Document",
      "XHR",
      "Fetch",
      "Ping",
      "Preflight",
      "Other",
    ]);
    expect(patterns.every((pattern) => pattern.urlPattern === "*")).toBe(true);
    expect(patterns.every((pattern) => pattern.requestStage === "Request")).toBe(true);
  });

  it("refuses every write the page tries to make, and lets reads through", async () => {
    const { cdp, callsTo, emit } = fakeCdp();
    await armAbandonGuard(cdp);

    // A read is not a leak: the page may keep loading its own assets.
    emit("Fetch.requestPaused", paused("r1", "GET", "http://127.0.0.1:8069/web/logo.png"));
    emit("Fetch.requestPaused", paused("r2", "HEAD", "http://127.0.0.1:8069/web/x.css"));
    await Promise.resolve();

    expect(callsTo("Fetch.continueRequest").map((call) => call.params["requestId"])).toEqual([
      "r1",
      "r2",
    ]);
    expect(callsTo("Fetch.failRequest")).toHaveLength(0);

    // The three ways a page leaves a record behind: a form submit, a fetch (with or without
    // `keepalive`) and a `sendBeacon`. All of them are requests, so all of them are refused.
    emit("Fetch.requestPaused", paused("w1", "POST", "http://127.0.0.1:8069/web/dataset/call_kw"));
    emit("Fetch.requestPaused", paused("w2", "PUT", "http://127.0.0.1:8069/web/beacon"));
    await Promise.resolve();

    const failures = callsTo("Fetch.failRequest");
    expect(failures.map((call) => call.params["requestId"])).toEqual(["w1", "w2"]);
    expect(failures[0]?.params).toMatchObject({ errorReason: BLOCKED_ERROR_REASON });
    expect(blockedRequests()).toEqual([
      { method: "POST", url: "http://127.0.0.1:8069/web/dataset/call_kw" },
      { method: "PUT", url: "http://127.0.0.1:8069/web/beacon" },
    ]);
  });

  it("releases the tab when it is disarmed, and reports what it held back", async () => {
    const { cdp, callsTo, emit } = fakeCdp();
    await armAbandonGuard(cdp);
    emit("Fetch.requestPaused", paused("w1", "POST", "http://127.0.0.1:8069/web/save"));
    await Promise.resolve();

    const blocked = await disarmAbandonGuard(cdp);

    expect(guardArmed()).toBe(false);
    expect(callsTo("Fetch.disable")).toHaveLength(1);
    expect(blocked).toEqual([{ method: "POST", url: "http://127.0.0.1:8069/web/save" }]);
    // The report is handed over once: a second release has nothing left to say.
    expect(await disarmAbandonGuard(cdp)).toEqual([]);

    // And an unarmed guard lets the page be: nothing is intercepted any more.
    emit("Fetch.requestPaused", paused("w2", "POST", "http://127.0.0.1:8069/web/save"));
    await Promise.resolve();
    expect(callsTo("Fetch.failRequest")).toHaveLength(1);
  });

  it("is idempotent, and keeps a page in a loop from growing the report without bound", async () => {
    const { cdp, callsTo, emit } = fakeCdp();
    await armAbandonGuard(cdp);
    await armAbandonGuard(cdp);
    expect(callsTo("Fetch.enable")).toHaveLength(1);

    for (let index = 0; index < BLOCKED_LIMIT + 10; index += 1) {
      emit("Fetch.requestPaused", paused(`w${index}`, "POST", `http://127.0.0.1:8069/web/${index}`));
    }
    await Promise.resolve();

    expect(blockedRequests()).toHaveLength(BLOCKED_LIMIT);
    expect(callsTo("Fetch.failRequest")).toHaveLength(BLOCKED_LIMIT + 10);
  });

  it("does not claim to be armed when the browser refused to intercept", async () => {
    const { cdp } = fakeCdp((method) => {
      if (method === "Fetch.enable") throw new Error("Fetch is not available on this target");
      return undefined;
    });

    await expect(armAbandonGuard(cdp)).rejects.toThrow(/not available/);
    expect(guardArmed()).toBe(false);
  });

  it("ignores a paused event that names no request", async () => {
    const { cdp, callsTo, emit } = fakeCdp();
    await armAbandonGuard(cdp);

    emit("Fetch.requestPaused", { request: { method: "POST" } });
    await Promise.resolve();

    expect(callsTo("Fetch.failRequest")).toHaveLength(0);
  });
});
