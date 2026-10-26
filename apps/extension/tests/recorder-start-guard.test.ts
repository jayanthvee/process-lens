// PL-039: the service worker pings the active tab's content script before a recording starts.
//
// A tab whose content script the extension reload orphaned answers nothing — starting a recording
// there records nothing at all. So the worker asks first, with a one-second timeout, and refuses
// with the refresh warning; a `chrome://` page or the Web Store is refused outright with its own
// line. The popup shows whichever warning the worker set, in place of the steps.
import { describe, expect, it, vi } from "vitest";

type Tab = { id?: number; url?: string };

interface GuardDeps {
  activeTab: () => Promise<Tab | undefined>;
  ping: (tabId: number, message: unknown) => Promise<unknown>;
  timeoutMs?: number;
  senderTabId?: number;
}

/** Everything the service worker touches at import time, so the module can load in jsdom. */
function chromeStub(): Record<string, unknown> {
  return {
    runtime: {
      id: "test-extension",
      onMessage: { addListener: () => undefined },
      onInstalled: { addListener: () => undefined },
      onStartup: { addListener: () => undefined },
      sendMessage: () => Promise.resolve(undefined),
      getManifest: () => ({ version: "0.1.0" }),
    },
    storage: {
      session: {
        get: () => Promise.resolve({}),
        set: () => Promise.resolve(undefined),
        remove: () => Promise.resolve(undefined),
      },
    },
    webNavigation: { onCommitted: { addListener: () => undefined } },
    action: {
      setBadgeText: () => Promise.resolve(undefined),
      setBadgeBackgroundColor: () => Promise.resolve(undefined),
    },
    tabs: {
      query: () => Promise.resolve([]),
      sendMessage: () => Promise.resolve(undefined),
      get: () => Promise.resolve(undefined),
      create: () => Promise.resolve(undefined),
    },
  };
}

(globalThis as Record<string, unknown>)["chrome"] = chromeStub();
const { startRecordingGuard, startRecordingOnActiveTab, unrecordablePage } = await import(
  "../src/background/service-worker"
);
const { REFRESH_PAGE_WARNING, UNRECORDABLE_PAGE_WARNING } = await import("../src/popup/words");

/** The happy tab: on the app's own page, content script alive. */
function deps(overrides: { tab?: Tab | null; ping?: (tabId: number) => Promise<unknown>; timeoutMs?: number } = {}): GuardDeps {
  const base: GuardDeps = {
    activeTab: async () => ({ id: 7, url: "https://crm.example.test/leads/new" }),
    ping: async () => ({ ok: true }),
  };
  return {
    activeTab: overrides.tab !== undefined ? async () => overrides.tab ?? undefined : base.activeTab,
    ping: overrides.ping ?? base.ping,
    ...(overrides.timeoutMs !== undefined ? { timeoutMs: overrides.timeoutMs } : {}),
  };
}

describe("the start guard (PL-039)", () => {
  it("starts when the tab answers the ping", async () => {
    await expect(startRecordingGuard(deps())).resolves.toEqual({ started: true });
  });

  it("does not start when the tab answers without ok", async () => {
    await expect(startRecordingGuard(deps({ ping: async () => ({ ok: false }) }))).resolves.toEqual({
      started: false,
      warning: REFRESH_PAGE_WARNING,
    });
  });

  it("does not start when the content script is gone (Receiving end does not exist)", async () => {
    const ping = async () => {
      throw new Error("Could not establish connection. Receiving end does not exist.");
    };
    await expect(startRecordingGuard(deps({ ping }))).resolves.toEqual({
      started: false,
      warning: REFRESH_PAGE_WARNING,
    });
  });

  it("does not start when the ping is never answered, within the one-second timeout", async () => {
    const ping = vi.fn(() => new Promise<never>(() => undefined));
    await expect(
      startRecordingGuard(deps({ ping, timeoutMs: 20 })),
    ).resolves.toEqual({ started: false, warning: REFRESH_PAGE_WARNING });
    expect(ping).toHaveBeenCalledWith(7, { type: "record:ping" });
  });

  it("does not start on a chrome:// page or the Web Store, and does not even ping", async () => {
    const ping = vi.fn(async () => ({ ok: true }));
    for (const url of [
      "chrome://extensions/",
      "https://chromewebstore.google.com/detail/processlens/abc",
      "https://chrome.google.com/webstore/detail/processlens/abc",
    ]) {
      const guard = await startRecordingGuard(deps({ tab: { id: 7, url }, ping }));
      expect(guard).toEqual({ started: false, warning: UNRECORDABLE_PAGE_WARNING });
    }
    expect(ping).not.toHaveBeenCalled();
  });

  it("starts when the active tab is the popup itself, opened as a tab (T1 review)", async () => {
    const ping = vi.fn(async () => {
      throw new Error("Could not establish connection. Receiving end does not exist.");
    });
    const guard = await startRecordingGuard({ ...deps({ ping }), senderTabId: 7 });
    expect(guard).toEqual({ started: true });
    expect(ping).not.toHaveBeenCalled();
  });

  it("warns when there is no page to ask", async () => {
    await expect(startRecordingGuard(deps({ tab: null }))).resolves.toEqual({
      started: false,
      warning: REFRESH_PAGE_WARNING,
    });
  });

  it("says the exact lines the ticket promises", () => {
    expect(REFRESH_PAGE_WARNING).toBe(
      "Refresh this page (press F5), then start recording again. ProcessLens was updated and can't see this page yet.",
    );
    expect(UNRECORDABLE_PAGE_WARNING).toBe(
      "ProcessLens can't record on this page. Open your app's page first.",
    );
  });
});

describe("unrecordablePage (PL-039)", () => {
  it("names chrome:// and the Web Store, and nothing else", () => {
    expect(unrecordablePage("chrome://extensions/")).toBe(true);
    expect(unrecordablePage("https://chromewebstore.google.com/detail/x")).toBe(true);
    expect(unrecordablePage("https://chrome.google.com/webstore/detail/x")).toBe(true);
    expect(unrecordablePage("https://crm.example.test/leads/new")).toBe(false);
    expect(unrecordablePage(undefined)).toBe(false);
    expect(unrecordablePage("not a url")).toBe(false);
  });
});

describe("starting a recording through the guard (PL-039)", () => {
  it("leaves the recording off and sets the warning when the tab is unreachable", async () => {
    const status = await startRecordingOnActiveTab(
      deps({
        ping: async () => {
          throw new Error("Could not establish connection. Receiving end does not exist.");
        },
      }),
    );
    expect(status.state).toBe("idle");
    expect(status.warning).toBe(REFRESH_PAGE_WARNING);
  });

  it("starts the recording when the tab answers", async () => {
    const status = await startRecordingOnActiveTab(deps());
    expect(status.state).toBe("recording");
    expect(status.warning).toBeNull();
  });

  it("clears an earlier warning once a recording starts", async () => {
    await startRecordingOnActiveTab(deps({ ping: async () => Promise.reject(new Error("no")) }));
    const status = await startRecordingOnActiveTab(deps());
    expect(status.state).toBe("recording");
    expect(status.warning).toBeNull();
  });
});

describe("a tab that was open before ProcessLens was installed (PL-103)", () => {
  it("gets the recorder put in, and recording starts, instead of asking for a refresh", async () => {
    let injected = false;
    const ping = vi.fn(async () => {
      if (!injected) throw new Error("Could not establish connection. Receiving end does not exist.");
      return { ok: true };
    });
    const inject = vi.fn(async (_tabId: number) => {
      injected = true;
    });

    const guard = await startRecordingGuard({ ...deps({ ping }), inject } as GuardDeps);

    expect(guard).toEqual({ started: true });
    expect(inject).toHaveBeenCalledWith(7);
  });

  it("asks for a refresh only when putting the recorder in did not work", async () => {
    const ping = async () => {
      throw new Error("Could not establish connection. Receiving end does not exist.");
    };
    const inject = vi.fn(async () => undefined);

    const guard = await startRecordingGuard({ ...deps({ ping }), inject } as GuardDeps);

    expect(guard).toEqual({ started: false, warning: REFRESH_PAGE_WARNING });
    expect(inject).toHaveBeenCalled();
  });
});
