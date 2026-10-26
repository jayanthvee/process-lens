/**
 * PL-107 item 14: the panel's key-column question says what it means, and defaults to the email.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { RecorderStatus, ToWorker } from "../../extension/src/shared/messages";
import type { RecordedEvent } from "../../extension/src/shared/recipe-types";
import { configureApi } from "../src/lib/api";
import { extensionChrome, type ExtensionChrome } from "../src/panel/extension";
import { PanelTeach } from "../src/panel/PanelTeach";
import { createFetchStub, settle, type FetchStub } from "./helpers";

const PAGE = "https://app.example.test/form";

const EVENTS: RecordedEvent[] = [
  {
    seq: 1,
    at: "2026-10-26T10:00:00Z",
    kind: "fill",
    url: PAGE,
    step: {
      id: "fill-1",
      action: "fill",
      targets: [{ by: "role", role: "textbox", name: "Name" }],
      value: { kind: "constant", value: "Priya Nair" },
      clear_first: true,
    },
  },
  {
    seq: 2,
    at: "2026-10-26T10:00:01Z",
    kind: "fill",
    url: PAGE,
    step: {
      id: "fill-2",
      action: "fill",
      targets: [{ by: "role", role: "textbox", name: "Email" }],
      value: { kind: "constant", value: "priya@example.com" },
      clear_first: true,
    },
  },
];

function statusFor(events: RecordedEvent[]): RecorderStatus {
  return {
    state: "idle",
    count: events.length,
    startedAt: null,
    wsUrl: "",
    wsConnected: false,
    warning: null,
  };
}

const originalChrome = extensionChrome();
let stub: FetchStub;

beforeEach(() => {
  configureApi({ base: "https://hub.example", headers: {} });
  stub = createFetchStub({});
  (globalThis as { chrome?: ExtensionChrome }).chrome = {
    runtime: {
      sendMessage: async (message: unknown) => {
        const typed = message as ToWorker;
        if (typed.type === "record:clear") {
          return { ok: true, status: statusFor([]), events: [] };
        }
        return { ok: true, status: statusFor(EVENTS), events: EVENTS };
      },
    },
    tabs: { query: async () => [{ id: 7 }] },
  } as unknown as ExtensionChrome;
});

afterEach(() => {
  stub.restore();
  cleanup();
  (globalThis as { chrome?: ExtensionChrome }).chrome = originalChrome;
  configureApi({ base: "", headers: {} });
});

describe("the key column question", () => {
  it("asks which value is different for every row, and says what it is for", async () => {
    render(<PanelTeach />);

    const key = await screen.findByRole("combobox", {
      name: "Which value is different for every row?",
    });
    expect(key).toBeTruthy();
    expect(screen.getByText("We use it so nothing gets added twice.")).toBeTruthy();
  });

  it("defaults to the email column, not the name", async () => {
    render(<PanelTeach />);

    const key = (await screen.findByRole("combobox", {
      name: "Which value is different for every row?",
    })) as HTMLSelectElement;
    await settle();
    expect(key.value).toBe("email");
  });
});
