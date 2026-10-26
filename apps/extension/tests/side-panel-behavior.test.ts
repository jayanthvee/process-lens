// PL-105: the worker sets Chrome's panel behavior, so a click on the toolbar icon opens the
// side panel rather than doing nothing.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  OPEN_PANEL_ON_CLICK,
  registerPanelBehavior,
  type PanelChrome,
} from "../src/background/side-panel";

interface FakeChrome extends PanelChrome {
  behaviors: { openPanelOnActionClick: boolean }[];
  fireInstalled(): void;
  fireStartup(): void;
}

function fakeChrome(withSidePanel = true): FakeChrome {
  const behaviors: { openPanelOnActionClick: boolean }[] = [];
  const installed: (() => void)[] = [];
  const startup: (() => void)[] = [];
  const chrome: FakeChrome = {
    behaviors,
    ...(withSidePanel
      ? {
          sidePanel: {
            setPanelBehavior: async (behavior: { openPanelOnActionClick: boolean }) => {
              behaviors.push(behavior);
            },
          },
        }
      : {}),
    runtime: {
      onInstalled: { addListener: (listener) => installed.push(listener) },
      onStartup: { addListener: (listener) => startup.push(listener) },
    },
    fireInstalled: () => {
      for (const listener of installed) listener();
    },
    fireStartup: () => {
      for (const listener of startup) listener();
    },
  };
  return chrome;
}

describe("the side panel's behavior", () => {
  it("is set when the extension is installed and when the browser starts", async () => {
    const chrome = fakeChrome();
    registerPanelBehavior(chrome);
    expect(chrome.behaviors).toEqual([]);

    chrome.fireInstalled();
    await Promise.resolve();
    chrome.fireStartup();
    await Promise.resolve();

    expect(chrome.behaviors).toEqual([OPEN_PANEL_ON_CLICK, OPEN_PANEL_ON_CLICK]);
    expect(OPEN_PANEL_ON_CLICK).toEqual({ openPanelOnActionClick: true });
  });

  it("does not stop the worker when Chrome has no sidePanel API", () => {
    const chrome = fakeChrome(false);
    registerPanelBehavior(chrome);

    expect(() => {
      chrome.fireInstalled();
      chrome.fireStartup();
    }).not.toThrow();
  });

  it("is wired into the service worker itself", () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const worker = readFileSync(resolve(here, "../src/background/service-worker.ts"), "utf8");

    expect(worker).toContain("registerPanelBehavior(chrome)");
  });
});
