/**
 * PL-105: the panel's Teach page, at the two places the popup's behaviour has to survive the move.
 *
 * Save: the panel posts the recording's last click as the commit step, exactly as the popup
 * preselected it. With no commit step the save click compiles as a plain click: no write-ahead and
 * no save check, so a crash mid-save can create the record twice.
 *
 * Step check: PL-103 taught the popup to find, after Stop, a step that cannot be repeated and to
 * offer its fix on that step's own row. The panel shows the same sentences with the same buttons.
 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { EMPTY_SPACE_PROBLEM, MID_FORM_PROBLEM } from "../../extension/src/popup/flow";
import type { RecorderStatus, ToWorker } from "../../extension/src/shared/messages";
import type { RecordedEvent } from "../../extension/src/shared/recipe-types";
import { configureApi } from "../src/lib/api";
import { extensionChrome, type ExtensionChrome } from "../src/panel/extension";
import { PanelTeach } from "../src/panel/PanelTeach";
import { createFetchStub, settle, type FetchStub } from "./helpers";

const NEW_FORM = "https://crm.example.com/leads/new";
const LIST = "https://crm.example.com/leads";

const EVENTS: RecordedEvent[] = [
  {
    seq: 1,
    at: "2026-10-26T10:00:00Z",
    kind: "click",
    url: LIST,
    step: {
      id: "step-form",
      action: "click",
      targets: [{ by: "role", role: "button", name: "Go to the form" }],
    },
  },
  {
    seq: 2,
    at: "2026-10-26T10:00:02Z",
    kind: "click",
    url: NEW_FORM,
    step: {
      id: "step-save",
      action: "click",
      targets: [{ by: "role", role: "button", name: "Save" }],
    },
  },
  // The page showed the save's result; it carries no step, so the save click stays the last click.
  {
    seq: 3,
    at: "2026-10-26T10:00:03Z",
    kind: "observation",
    url: "https://crm.example.com/leads/42",
    step: null,
  },
];

/** A long run of page text: what a click on the page's background records as its role name. */
const PAGE_TEXT =
  "New lead Fill in the details of the opportunity, then save it to the pipeline. ".repeat(3);

/** PL-103: the same two bad shapes the popup's own test uses. */
const AREA_CLICK: RecordedEvent[] = [
  {
    seq: 1,
    at: "2026-10-26T10:00:00Z",
    kind: "click",
    url: LIST,
    step: {
      id: "click-1",
      action: "click",
      targets: [{ by: "role", role: "button", name: "New" }],
    },
  },
  {
    seq: 2,
    at: "2026-10-26T10:00:01Z",
    kind: "click",
    url: NEW_FORM,
    step: {
      id: "click-2",
      action: "click",
      targets: [{ by: "role", role: "generic", name: PAGE_TEXT }],
    },
  },
];

const MID_FORM: RecordedEvent[] = [
  {
    seq: 1,
    at: "2026-10-26T10:00:00Z",
    kind: "fill",
    url: NEW_FORM,
    step: {
      id: "fill-1",
      action: "fill",
      name: "Type in Opportunity",
      targets: [{ by: "role", role: "textbox", name: "Opportunity" }],
      value: { kind: "constant", value: "x" },
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
const sent: ToWorker[] = [];

/** Answer recorder messages from `events`; a clear leaves the worker with nothing recorded. */
function installChrome(events: RecordedEvent[]): void {
  (globalThis as { chrome?: ExtensionChrome }).chrome = {
    runtime: {
      sendMessage: async (message: unknown) => {
        const typed = message as ToWorker;
        sent.push(typed);
        if (typed.type === "record:clear") {
          return { ok: true, status: statusFor([]), events: [] };
        }
        return { ok: true, status: statusFor(events), events };
      },
    },
  };
}

let stub: FetchStub;

beforeEach(() => {
  sent.length = 0;
  configureApi({ base: "https://hub.example", headers: {} });
  stub = createFetchStub({
    "/api/v1/recipes/compile": {
      body: { recipe: { name: "Recorded task" }, warnings: [], search_kind: null },
    },
    "/api/v1/tasks": { body: { workflow_id: "w-1" } },
  });
  installChrome(EVENTS);
});

afterEach(() => {
  stub.restore();
  cleanup();
  (globalThis as { chrome?: ExtensionChrome }).chrome = originalChrome;
  configureApi({ base: "", headers: {} });
});

describe("the panel's Save", () => {
  it("sends the recording's last click as the commit step", async () => {
    render(<PanelTeach />);

    await screen.findByText("Clicked Save");
    fireEvent.click(screen.getByRole("button", { name: "Save as task" }));
    await settle();

    const compile = stub.requests.find((call) => call.path === "/api/v1/recipes/compile");
    expect(compile?.method).toBe("POST");
    const body = compile?.body as {
      commit_step_ids?: unknown;
      recording?: { step: { id: string; action: string } | null }[];
    };
    expect(body.commit_step_ids).toEqual(["step-save"]);
    // The commit names a step the request really carries, so the recipe the route compiles from
    // it marks that step (see tests/api/test_compile_api.py's panel-save test).
    const clicks = (body.recording ?? [])
      .map((event) => event.step)
      .filter((step) => step?.action === "click")
      .map((step) => step?.id);
    expect(clicks).toContain("step-save");
    // The compile is followed by the save that makes the task real.
    expect(stub.calls).toContain("/api/v1/tasks");
  });
});

describe("the panel's step check", () => {
  it("says a click on the page's empty space cannot be repeated, and Remove deletes it", async () => {
    installChrome(AREA_CLICK);
    render(<PanelTeach />);

    await screen.findByText("Clicked New");
    const bad = screen
      .getAllByRole("listitem")
      .find((row) => row.textContent?.includes(EMPTY_SPACE_PROBLEM));
    expect(bad).toBeTruthy();
    // The sentence sits on the row of the step it is about, not in a list of its own.
    expect(bad?.textContent).toContain("Clicked");

    fireEvent.click(within(bad as HTMLElement).getByRole("button", { name: "Remove" }));
    await settle();

    expect(sent.some((message) => message.type === "record:remove" && message.seq === 2)).toBe(
      true,
    );
  });

  it("says teaching began mid-form, and Start over clears the recording", async () => {
    installChrome(MID_FORM);
    render(<PanelTeach />);

    await screen.findByText("Typed the opportunity");
    const bad = screen
      .getAllByRole("listitem")
      .find((row) => row.textContent?.includes(MID_FORM_PROBLEM));
    expect(bad).toBeTruthy();

    fireEvent.click(within(bad as HTMLElement).getByRole("button", { name: "Start over" }));
    await settle();

    expect(sent.some((message) => message.type === "record:clear")).toBe(true);
    // The clear empties the recording, so its one bad step is gone.
    expect(screen.queryByText("Typed the opportunity")).toBeNull();
  });

  it("says nothing about a recording that can be repeated", async () => {
    render(<PanelTeach />);

    await screen.findByText("Clicked Save");
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Start over" })).toBeNull();
  });
});
