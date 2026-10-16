// The abandon guard (PL-027): a record that leaves the tab without committing must not take its
// half-filled form with it.
//
// The page's behaviour being modelled is Odoo's: it autosaves a dirty form when the document
// unloads, so a record the runner parked would be written by the destination itself, with no
// commit step and no approval. The guard shuts the tab's network in the browser, retires the dirty
// document to a blank page, and reopens the network once the blank page has arrived.
import { beforeEach, describe, expect, it } from "vitest";
import {
  BLANK_URL,
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

/** A request as CDP's `Network.requestWillBeSent` reports it. */
function sent(method: string, url: string): Record<string, unknown> {
  return { requestId: url, request: { method, url } };
}

describe("the abandon guard", () => {
  it("blocks the network, retires the page to a blank one, then reopens the network", async () => {
    const { cdp, calls } = fakeCdp();

    await armAbandonGuard(cdp);

    expect(guardArmed()).toBe(true);
    const order = calls
      .map((call) =>
        call.method === "Network.setBlockedURLs"
          ? `block ${JSON.stringify(call.params["urls"])}`
          : call.method,
      )
      .filter((step) => step !== "Runtime.evaluate");
    // The block is in place before the page is left, and lifted only after the blank page arrived.
    expect(order).toEqual([
      "Network.enable",
      'block ["*"]',
      "Page.navigate",
      "block []",
      "Network.disable",
    ]);
    expect(calls.find((call) => call.method === "Page.navigate")?.params).toEqual({ url: BLANK_URL });
    // The extension never answers requests one by one: that is what starved its worker on Odoo.
    expect(calls.some((call) => call.method.startsWith("Fetch."))).toBe(false);
  });

  it("keeps the writes the dying page tried to send, and ignores its reads", async () => {
    const { cdp, emit } = fakeCdp((method) => {
      if (method === "Page.navigate") {
        // The unload handlers run as the page is left, while the network is blocked.
        emit("Network.requestWillBeSent", sent("GET", "http://127.0.0.1:8069/web/logo.png"));
        emit("Network.requestWillBeSent", sent("POST", "http://127.0.0.1:8069/web/dataset/call_kw"));
        emit("Network.requestWillBeSent", sent("POST", "http://127.0.0.1:8069/web/beacon"));
      }
      return undefined;
    });

    await armAbandonGuard(cdp);

    expect(blockedRequests()).toEqual([
      { method: "POST", url: "http://127.0.0.1:8069/web/dataset/call_kw" },
      { method: "POST", url: "http://127.0.0.1:8069/web/beacon" },
    ]);
    const report = await disarmAbandonGuard(cdp);
    expect(report).toHaveLength(2);
    expect(guardArmed()).toBe(false);
    expect(blockedRequests()).toEqual([]);
  });

  it("accepts a leave-this-page dialog, and no other kind", async () => {
    const { cdp, emit, callsTo } = fakeCdp((method) => {
      if (method === "Page.navigate") {
        emit("Page.javascriptDialogOpening", { type: "alert" });
        emit("Page.javascriptDialogOpening", { type: "beforeunload" });
      }
      return undefined;
    });

    await armAbandonGuard(cdp);

    expect(callsTo("Page.handleJavaScriptDialog")).toEqual([
      { method: "Page.handleJavaScriptDialog", params: { accept: true } },
    ]);
  });

  it("is idempotent, and keeps a page in a loop from growing the report without bound", async () => {
    const { cdp, emit, callsTo } = fakeCdp((method) => {
      if (method === "Page.navigate") {
        for (let index = 0; index < BLOCKED_LIMIT + 20; index += 1) {
          emit("Network.requestWillBeSent", sent("POST", `http://127.0.0.1:8069/web/${index}`));
        }
      }
      return undefined;
    });

    await armAbandonGuard(cdp);
    await armAbandonGuard(cdp);

    expect(callsTo("Page.navigate")).toHaveLength(1);
    expect(blockedRequests()).toHaveLength(BLOCKED_LIMIT);
  });

  it("fails, and still reopens the network, when the tab never reaches the blank page", async () => {
    const { cdp, callsTo } = fakeCdp((method, params) => {
      if (method === "Runtime.evaluate" && String(params["expression"]).trim() === "location.href") {
        return { result: { value: "http://127.0.0.1:8069/odoo/crm/new" } };
      }
      return undefined;
    });

    await expect(armAbandonGuard(cdp, 250)).rejects.toThrow(/did not reach about:blank/);

    expect(guardArmed()).toBe(false);
    const blocks = callsTo("Network.setBlockedURLs").map((call) => call.params["urls"]);
    expect(blocks.at(-1)).toEqual([]);
  });

  it("fails without navigating when the browser refuses to block", async () => {
    const { cdp, callsTo } = fakeCdp((method, params) => {
      if (method === "Network.setBlockedURLs" && JSON.stringify(params["urls"]) === '["*"]') {
        throw new Error("refused");
      }
      return undefined;
    });

    await expect(armAbandonGuard(cdp)).rejects.toThrow(/refused/);

    expect(callsTo("Page.navigate")).toHaveLength(0);
    expect(guardArmed()).toBe(false);
  });
});
