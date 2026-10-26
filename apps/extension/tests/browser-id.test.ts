// PL-087: each install's browser id, and the dashboard asking for it.
import { describe, expect, it } from "vitest";

import { BROWSER_ID_KEY, browserId, originOf } from "../src/background/browser-id";
import {
  BROWSER_ANSWER,
  SHOW_WINDOW,
  WHICH_BROWSER,
  installBrowserBridge,
} from "../src/content/browser-bridge";

function memoryStore(): { data: Record<string, unknown>; get: (k: string[]) => Promise<Record<string, unknown>>; set: (i: Record<string, unknown>) => Promise<void> } {
  const data: Record<string, unknown> = {};
  return {
    data,
    get: async (keys) => Object.fromEntries(keys.filter((k) => k in data).map((k) => [k, data[k]])),
    set: async (items) => {
      Object.assign(data, items);
    },
  };
}

describe("the browser id", () => {
  it("is made once, in the shape the API accepts, and kept", async () => {
    const store = memoryStore();
    const first = await browserId(store);
    const again = await browserId(store);

    expect(first).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
    expect(again).toBe(first);
    expect(store.data[BROWSER_ID_KEY]).toBe(first);
  });

  it("differs between installs", async () => {
    expect(await browserId(memoryStore())).not.toBe(await browserId(memoryStore()));
  });

  it("reads an address's origin, and nothing from a non-address", () => {
    expect(originOf("https://x.onrender.com/")).toBe("https://x.onrender.com");
    expect(originOf("")).toBe("");
    expect(originOf(undefined)).toBe("");
  });
});

describe("the dashboard bridge", () => {
  // A stand-in window: the browser sets `source` to the posting window, which a test DOM may not.
  async function ask(hubOrigin: string, fromSelf = true): Promise<unknown[]> {
    const answers: unknown[] = [];
    const listeners: Array<(event: MessageEvent) => void> = [];
    const fake = {
      location: { origin: "https://processlens-api-a2bl.onrender.com" },
      addEventListener: (_type: string, fn: (event: MessageEvent) => void) => {
        listeners.push(fn);
      },
      postMessage: (data: unknown, origin: string) => answers.push({ data, origin }),
    } as unknown as Window & { top: Window };
    (fake as { top: Window }).top = fake;
    installBrowserBridge(fake, async () => ({ browserId: "browser-anna-0001", hubOrigin }));
    listeners[0]?.({ source: fromSelf ? fake : {}, data: { type: WHICH_BROWSER } } as MessageEvent);
    await new Promise((resolve) => setTimeout(resolve, 0));
    return answers;
  }

  it("answers only the ProcessLens address it last connected to", async () => {
    expect(await ask("https://processlens-api-a2bl.onrender.com")).toEqual([
      {
        data: { type: BROWSER_ANSWER, browserId: "browser-anna-0001" },
        origin: "https://processlens-api-a2bl.onrender.com",
      },
    ]);
  });

  it("tells any other site nothing, and ignores messages from other windows", async () => {
    expect(await ask("https://processlens-api.example")).toEqual([]);
    expect(await ask("https://processlens-api-a2bl.onrender.com", false)).toEqual([]);
  });
});

describe("Show the ProcessLens window (PL-104)", () => {
  async function askToShow(hubOrigin: string, fromSelf = true): Promise<number> {
    let shown = 0;
    const listeners: Array<(event: MessageEvent) => void> = [];
    const fake = {
      location: { origin: "https://processlens-api-a2bl.onrender.com" },
      addEventListener: (_type: string, fn: (event: MessageEvent) => void) => {
        listeners.push(fn);
      },
      postMessage: () => undefined,
    } as unknown as Window & { top: Window };
    (fake as { top: Window }).top = fake;
    installBrowserBridge(
      fake,
      async () => ({ browserId: "browser-anna-0001", hubOrigin }),
      async () => {
        shown += 1;
      },
    );
    listeners[0]?.({ source: fromSelf ? fake : {}, data: { type: SHOW_WINDOW } } as MessageEvent);
    await new Promise((resolve) => setTimeout(resolve, 0));
    return shown;
  }

  it("brings the window forward when the ProcessLens run page asks", async () => {
    expect(await askToShow("https://processlens-api-a2bl.onrender.com")).toBe(1);
  });

  it("does nothing for any other site or window", async () => {
    expect(await askToShow("https://processlens-api.example")).toBe(0);
    expect(await askToShow("https://processlens-api-a2bl.onrender.com", false)).toBe(0);
  });
});
