// The abandon guard (PL-027): a record that leaves the tab without committing must not take its
// half-filled form with it.
//
// The page's behaviour being modelled is Odoo's: it autosaves a dirty form when the document
// unloads, so a record the runner parked would be written by the destination itself, with no
// commit step and no approval. The guard shuts the tab's network in the browser, retires the dirty
// document to a blank page, and reopens the network once the blank page has arrived.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  BLANK_URL,
  BLOCKED_LIMIT,
  GUARD_BINDING,
  armAbandonGuard,
  blockedRequests,
  disarmAbandonGuard,
  guardArmed,
  resetAbandonGuard,
} from "../src/executor/abandon-guard";
import { fakeCdp } from "./helpers/fake-cdp";

const pageBeacon = Object.getOwnPropertyDescriptor(navigator, "sendBeacon");
const pageFetch = window.fetch;

beforeEach(() => {
  resetAbandonGuard();
});

afterEach(() => {
  // The guard silences the "page", which in these tests is jsdom's own window.
  if (pageBeacon) Object.defineProperty(navigator, "sendBeacon", pageBeacon);
  else delete (navigator as unknown as Record<string, unknown>)["sendBeacon"];
  window.fetch = pageFetch;
  delete (window as unknown as Record<string, unknown>)[GUARD_BINDING];
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
    // The page is silenced and the block is in place before the page is left, and both are
    // lifted only after the blank page arrived.
    expect(order).toEqual([
      "Runtime.addBinding",
      "Network.enable",
      'block ["*"]',
      "Page.navigate",
      "Runtime.removeBinding",
      "block []",
      "Network.disable",
    ]);
    const evaluated = calls.map((call) =>
      String(call.params["expression"] ?? ""),
    );
    const silence = evaluated.findIndex((expression) =>
      expression.includes("sendBeacon"),
    );
    const navigate = calls.findIndex((call) => call.method === "Page.navigate");
    expect(silence).toBeGreaterThan(-1);
    expect(silence).toBeLessThan(navigate);
    expect(
      calls.find((call) => call.method === "Page.navigate")?.params,
    ).toEqual({ url: BLANK_URL });
    // The extension never answers requests one by one: that is what starved its worker on Odoo.
    expect(calls.some((call) => call.method.startsWith("Fetch."))).toBe(false);
  });

  it("keeps the writes the dying page tried to send, and ignores its reads", async () => {
    const { cdp, emit } = fakeCdp((method) => {
      if (method === "Page.navigate") {
        // The unload handlers run as the page is left, while the network is blocked.
        emit(
          "Network.requestWillBeSent",
          sent("GET", "http://127.0.0.1:8069/web/logo.png"),
        );
        emit(
          "Network.requestWillBeSent",
          sent("POST", "http://127.0.0.1:8069/web/dataset/call_kw"),
        );
        emit(
          "Network.requestWillBeSent",
          sent("POST", "http://127.0.0.1:8069/web/beacon"),
        );
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
          emit(
            "Network.requestWillBeSent",
            sent("POST", `http://127.0.0.1:8069/web/${index}`),
          );
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
      if (
        method === "Runtime.evaluate" &&
        String(params["expression"]).trim() === "location.href"
      ) {
        return { result: { value: "http://127.0.0.1:8069/odoo/crm/new" } };
      }
      return undefined;
    });

    await expect(armAbandonGuard(cdp, 250)).rejects.toThrow(
      /did not reach about:blank/,
    );

    expect(guardArmed()).toBe(false);
    const blocks = callsTo("Network.setBlockedURLs").map(
      (call) => call.params["urls"],
    );
    expect(blocks.at(-1)).toEqual([]);
  });

  it("fails without navigating when the browser refuses to block", async () => {
    const { cdp, callsTo } = fakeCdp((method, params) => {
      if (
        method === "Network.setBlockedURLs" &&
        JSON.stringify(params["urls"]) === '["*"]'
      ) {
        throw new Error("refused");
      }
      return undefined;
    });

    await expect(armAbandonGuard(cdp)).rejects.toThrow(/refused/);

    expect(callsTo("Page.navigate")).toHaveLength(0);
    expect(guardArmed()).toBe(false);
  });

  describe("PL-098: the sends that leave past the network block", () => {
    /** A fake whose binding reaches the guard the way CDP's `Runtime.bindingCalled` does. */
    function withBinding(onNavigate: () => void) {
      const fake = fakeCdp((method, params) => {
        if (method === "Runtime.addBinding") {
          const name = String(params["name"]);
          (window as unknown as Record<string, unknown>)[name] = (
            payload: string,
          ) => fake.emit("Runtime.bindingCalled", { name, payload });
        }
        if (method === "Page.navigate") onNavigate();
        return undefined;
      });
      return fake;
    }

    it("a beacon the dying page sends goes nowhere, and is reported", async () => {
      const sentBeacons: string[] = [];
      Object.defineProperty(navigator, "sendBeacon", {
        configurable: true,
        writable: true,
        value: (url: string) => {
          sentBeacons.push(url);
          return true;
        },
      });
      let answered: boolean | undefined;
      const { cdp } = withBinding(() => {
        // Odoo's form, on the way out: save what it holds.
        answered = navigator.sendBeacon(
          "http://127.0.0.1:8069/web/dataset/call_kw/crm.lead/web_save",
          "{}",
        );
      });

      await armAbandonGuard(cdp);

      expect(sentBeacons).toEqual([]);
      expect(answered).toBe(false);
      expect(blockedRequests()).toEqual([
        {
          method: "POST",
          url: "http://127.0.0.1:8069/web/dataset/call_kw/crm.lead/web_save",
        },
      ]);
    });

    it("a keepalive fetch is refused and reported, and an ordinary fetch is left to the block", async () => {
      const fetched: string[] = [];
      window.fetch = (async (input: RequestInfo | URL) => {
        fetched.push(String(input));
        return new Response("{}");
      }) as typeof fetch;
      let keepalive: Promise<unknown> | undefined;
      let ordinary: Promise<unknown> | undefined;
      const { cdp } = withBinding(() => {
        keepalive = window.fetch("http://127.0.0.1:8069/web/save", {
          method: "post",
          keepalive: true,
        });
        ordinary = window.fetch("http://127.0.0.1:8069/web/ping");
      });

      await armAbandonGuard(cdp);

      await expect(keepalive).rejects.toThrow(/refused/);
      await ordinary;
      expect(fetched).toEqual(["http://127.0.0.1:8069/web/ping"]);
      expect(blockedRequests()).toEqual([
        { method: "POST", url: "http://127.0.0.1:8069/web/save" },
      ]);
    });

    it("does not leave a page it could not silence", async () => {
      const { cdp, callsTo } = fakeCdp((method, params) => {
        if (
          method === "Runtime.evaluate" &&
          String(params["expression"]).includes("sendBeacon")
        ) {
          return { exceptionDetails: { text: "Uncaught" } };
        }
        return undefined;
      });

      await expect(armAbandonGuard(cdp)).rejects.toThrow(
        /could not be silenced/,
      );

      expect(callsTo("Page.navigate")).toHaveLength(0);
      expect(guardArmed()).toBe(false);
      expect(callsTo("Runtime.removeBinding")).toHaveLength(1);
    });
  });
});
