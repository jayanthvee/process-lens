// PL-107 item 12: the side panel's half of a teaching-time code page.
//
// The person is told when the code is being read from their Gmail, offered the same PL-102 yes
// when they never gave it, and told plainly when no single code was found so they can type it.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { RecorderStatus, ToWorker } from "../../extension/src/shared/messages";
import {
  CODE_FROM_EMAIL_BUTTON,
  CODE_FROM_EMAIL_OFFER,
  codeFromEmailWords,
} from "../src/lib/words";
import { extensionChrome, type ExtensionChrome } from "../src/panel/extension";
import { PanelTeach } from "../src/panel/PanelTeach";

const originalChrome = extensionChrome();
const sent: ToWorker[] = [];
let status: RecorderStatus;

function statusWith(code: RecorderStatus["code"]): RecorderStatus {
  return {
    state: "recording",
    count: 0,
    startedAt: null,
    wsUrl: "",
    wsConnected: false,
    warning: null,
    code,
  };
}

function installChrome(): void {
  (globalThis as { chrome?: ExtensionChrome }).chrome = {
    runtime: {
      sendMessage: async (message: unknown) => {
        sent.push(message as ToWorker);
        return { ok: true, status, events: [] };
      },
    },
    tabs: { query: async () => [{ id: 7 }] },
  } as unknown as ExtensionChrome;
}

beforeEach(() => {
  sent.length = 0;
  installChrome();
});

afterEach(() => {
  cleanup();
  (globalThis as { chrome?: ExtensionChrome }).chrome = originalChrome;
});

describe("the panel while a code page is being filled", () => {
  it("says it is reading the code from Gmail", async () => {
    status = statusWith({ state: "looking" });
    render(<PanelTeach />);

    await screen.findByText("Reading your code from Gmail…");
  });

  it("offers the PL-102 yes, and the person's yes is sent", async () => {
    status = statusWith({ state: "offer" });
    render(<PanelTeach />);

    await screen.findByText(CODE_FROM_EMAIL_OFFER);
    fireEvent.click(screen.getByRole("button", { name: CODE_FROM_EMAIL_BUTTON }));

    expect(sent.some((message) => message.type === "record:code-consent")).toBe(true);
  });

  it("says no single code was found, in words the person can act on", async () => {
    status = statusWith({ state: "missed", reason: "ambiguous" });
    render(<PanelTeach />);

    await screen.findByText(codeFromEmailWords("ambiguous"));
  });

  it("says nothing about a code when there is no code page", async () => {
    status = statusWith(null);
    render(<PanelTeach />);

    await screen.findByText("Recording");
    expect(sent.some((message) => message.type === "record:code-consent")).toBe(false);
    expect(screen.queryByText(CODE_FROM_EMAIL_OFFER)).toBeNull();
  });
});
