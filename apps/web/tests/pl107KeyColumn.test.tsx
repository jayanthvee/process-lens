/**
 * PL-109 items 5 and 6: the key value and the save click are decided and shown, not asked.
 *
 * The panel says "We'll use Email so nothing gets added twice. Change" when a value can identify a
 * row, and asks "Which value is different for every row?" only when it cannot. The save click reads
 * "The View results click saves your work. Change", and the question is under Change.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { RecorderStatus, ToWorker } from "../../extension/src/shared/messages";
import type { RecordedEvent } from "../../extension/src/shared/recipe-types";
import { configureApi } from "../src/lib/api";
import { extensionChrome, type ExtensionChrome } from "../src/panel/extension";
import { PanelTeach } from "../src/panel/PanelTeach";
import { createFetchStub, settle, type FetchStub } from "./helpers";

const PAGE = "https://app.example.test/form";

function fill(seq: number, name: string, value: string): RecordedEvent {
  return {
    seq,
    at: "2026-10-26T10:00:00Z",
    kind: "fill",
    url: PAGE,
    step: {
      id: `fill-${seq}`,
      action: "fill",
      targets: [{ by: "role", role: "textbox", name }],
      value: { kind: "constant", value },
      clear_first: true,
    },
  };
}

const IDENTIFIABLE: RecordedEvent[] = [
  fill(1, "Name", "Priya Nair"),
  fill(2, "Email", "priya@example.com"),
];

const UNIDENTIFIABLE: RecordedEvent[] = [
  fill(1, "Interest", "Data course"),
  fill(2, "Notes", "Called on Monday"),
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

function installChrome(events: RecordedEvent[]): void {
  (globalThis as { chrome?: ExtensionChrome }).chrome = {
    runtime: {
      sendMessage: async (message: unknown) => {
        const typed = message as ToWorker;
        if (typed.type === "record:clear") {
          return { ok: true, status: statusFor([]), events: [] };
        }
        return { ok: true, status: statusFor(events), events };
      },
    },
    tabs: { query: async () => [{ id: 7 }] },
  } as unknown as ExtensionChrome;
}

beforeEach(() => {
  configureApi({ base: "https://hub.example", headers: {} });
  stub = createFetchStub({});
});

afterEach(() => {
  stub.restore();
  cleanup();
  (globalThis as { chrome?: ExtensionChrome }).chrome = originalChrome;
  configureApi({ base: "", headers: {} });
});

describe("the key value is decided, not asked", () => {
  it("says which value it will use, with a Change to change it", async () => {
    installChrome(IDENTIFIABLE);
    render(<PanelTeach />);

    await screen.findByText("Email", { selector: "b" });
    expect(screen.getByText(/so nothing gets added twice/i)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Change the value that is different for every row" }),
    ).toBeTruthy();
    // The question is not on screen until Change is pressed.
    expect(
      screen.queryByRole("combobox", { name: "Which value is different for every row?" }),
    ).toBeNull();
  });

  it("opens the question under Change", async () => {
    installChrome(IDENTIFIABLE);
    render(<PanelTeach />);

    await screen.findByText("Email", { selector: "b" });
    fireEvent.click(
      screen.getByRole("button", { name: "Change the value that is different for every row" }),
    );
    await settle();

    const choice = screen.getByRole("combobox", {
      name: "Which value is different for every row?",
    });
    expect((choice as HTMLSelectElement).value).toBe("email");
  });

  it("asks when no value can identify a row", async () => {
    installChrome(UNIDENTIFIABLE);
    render(<PanelTeach />);

    expect(
      await screen.findByRole("combobox", { name: "Which value is different for every row?" }),
    ).toBeTruthy();
    expect(screen.queryByText(/We'll use/i)).toBeNull();
  });
});

describe("the save click is decided, not asked", () => {
  const withClicks: RecordedEvent[] = [
    ...IDENTIFIABLE,
    {
      seq: 3,
      at: "2026-10-26T10:00:02Z",
      kind: "click",
      url: PAGE,
      step: {
        id: "click-3",
        action: "click",
        targets: [{ by: "role", role: "button", name: "Save" }],
      },
    },
  ];

  it("says which click saves, with a Change to change it", async () => {
    installChrome(withClicks);
    render(<PanelTeach />);

    await screen.findByText("Save", { selector: "b" });
    expect(screen.getByText(/click saves your work/i)).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Which click saves your work?" })).toBeNull();
  });

  it("opens the save-click question under Change", async () => {
    installChrome(withClicks);
    render(<PanelTeach />);

    await screen.findByText("Save", { selector: "b" });
    fireEvent.click(screen.getByRole("button", { name: "Change which click saves your work" }));
    await settle();

    const choice = screen.getByRole("combobox", {
      name: "Which click saves your work?",
    }) as HTMLSelectElement;
    expect(Array.from(choice.options).map((option) => option.text)).toEqual([
      "Clicked Save",
      "Nothing gets saved",
    ]);
  });
});
