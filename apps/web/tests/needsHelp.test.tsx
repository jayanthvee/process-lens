/**
 * Needs help: the safety gate, the request body, the endpoints, the operator's 503 and a 409's own
 * words beside the button that caused it.
 *
 * These are PL-015's `commitGate.test.ts` and the operator half of `errorDetail.test.ts` carried
 * forward as React tests: requirement 6 of PL-023 says the safety rule is unchanged and mandatory,
 * so the same assertions hold here. The pure decisions live in `needsHelpActions.ts` and are called
 * directly as well as through the page.
 */

import { fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  DESTINATION_CHECK_LABEL,
  OPERATOR_PROBLEM,
  actionBody,
  actionGate,
  recordActionPath,
  recordActions,
} from "../src/pages/needsHelpActions";
import type { NeedsHelpRow } from "../src/types";
import {
  callsTo,
  createFetchStub,
  renderApp,
  settle,
  type StubRoutes,
} from "./helpers";

const OPERATOR_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";

/** The API's own words for a missing operator identity (services/api/main.py). */
const OPERATOR_503 = "PROCESSLENS_OPERATOR_ID is not set, so there is no operator id to act as";

/*
 * The 409 the API really sends comes from the ledger itself: `str(exc)` of a `StateConflict` or the
 * database trigger's own text (services/api/runs.py, packages/ledger/records.py,
 * db/migrations/0003_run_orchestration.sql). It is written for an engineer -- it names a state and
 * the table row -- so it must never be what the person reads. These two constants are the two
 * shapes that actually arrive: the state conflict and the trigger's refusal.
 */
const REAL_CONFLICT = "run_record 3f0e1c2a-0000-4000-8000-00000000abcd is not parked";
const TRIGGER_CONFLICT =
  "run_record 3f0e1c2a-0000-4000-8000-00000000abcd was commit-attempted; parked -> skipped requires a fresh destination check";
/** What the person reads instead: one sentence about what happened and what to do. */
const DEALT_WITH =
  "Someone else has already dealt with this row. Reload the page to see where it stands.";
/** The same, when the refusal's shape is one we cannot characterise at all. */
const REFUSAL_FALLBACK = "The API refused that. Reload the page and try again.";

function sampleRow(over: Partial<NeedsHelpRow> = {}): NeedsHelpRow {
  return {
    record_id: "record-1",
    run_id: "run-1",
    task_name: "Add inquiries",
    row: "ACME-1",
    state: "parked",
    reason: "missing email",
    commit_attempted: true,
    ...over,
  };
}

function routes(rows: NeedsHelpRow[], extra: StubRoutes = {}): StubRoutes {
  return {
    "/api/v1/needs-help": { body: rows },
    "/api/v1/operator": { body: { operator_id: OPERATOR_ID } },
    "/api/v1/executor/session": { body: { attached: true } },
    ...extra,
  };
}

function button(container: HTMLElement, selector: string): HTMLButtonElement {
  const node = container.querySelector<HTMLButtonElement>(selector);
  if (node === null) throw new Error(`no button matching ${selector}`);
  return node;
}

function checkbox(container: HTMLElement): HTMLInputElement {
  const node = container.querySelector<HTMLInputElement>(".destination-check-box");
  if (node === null) throw new Error("no destination-check checkbox");
  return node;
}

function posts(stub: { requests: { method: string; path: string; body: unknown }[] }, path: string) {
  return stub.requests.filter((call) => call.method === "POST" && call.path === path);
}

describe("the gate itself", () => {
  it("names both requirements and no more", () => {
    const gate = actionGate({
      state: "parked",
      commitAttempted: true,
      confirmed: false,
      operatorId: OPERATOR_ID,
    });
    expect(gate.needsConfirmation).toBe(true);
    expect(gate.needsOperator).toBe(true);
    expect(gate.disabled).toBe(true);
    expect(gate.shown).toBe(true);
  });

  it("leaves a row that does not need a person alone", () => {
    const gate = actionGate({
      state: "verified",
      commitAttempted: true,
      confirmed: true,
      operatorId: OPERATOR_ID,
    });
    expect(gate.shown).toBe(false);
    expect(recordActions("verified")).toEqual({ requeue: false, dismiss: false });
  });

  it("stays disabled while the API has no operator id to send", () => {
    const gate = actionGate({
      state: "parked",
      commitAttempted: true,
      confirmed: true,
      operatorId: null,
    });
    expect(gate.disabled).toBe(true);
  });

  it("asks nothing extra of a row that never reached a commit", () => {
    const gate = actionGate({
      state: "failed",
      commitAttempted: false,
      confirmed: false,
      operatorId: null,
    });
    expect(gate.needsConfirmation).toBe(false);
    expect(gate.needsOperator).toBe(false);
    expect(gate.disabled).toBe(false);
  });

  it("disables every action when the operator endpoint refused", () => {
    const gate = actionGate({
      state: "failed",
      commitAttempted: false,
      confirmed: false,
      operatorId: null,
      operatorUnavailable: true,
    });
    expect(gate.disabled).toBe(true);
    // The id is not what a plain row needs; the page simply has nobody to record.
    expect(gate.needsOperator).toBe(false);
  });
});

describe("the request body", () => {
  it("carries the operator id only when a commit was attempted and the check was confirmed", () => {
    expect(
      actionBody({ commitAttempted: false, confirmed: false, operatorId: OPERATOR_ID }),
    ).toEqual({});
    expect(
      actionBody({ commitAttempted: true, confirmed: true, operatorId: OPERATOR_ID }),
    ).toEqual({ destination_checked_by: OPERATOR_ID });
    expect(() =>
      actionBody({ commitAttempted: true, confirmed: false, operatorId: OPERATOR_ID }),
    ).toThrow();
    expect(() =>
      actionBody({ commitAttempted: true, confirmed: true, operatorId: null }),
    ).toThrow();
  });
});

describe("the endpoints", () => {
  it("builds the two paths the API already serves", () => {
    expect(recordActionPath("record-1", "requeue")).toBe("/api/v1/records/record-1/requeue");
    expect(recordActionPath("record-1", "dismiss")).toBe("/api/v1/records/record-1/dismiss");
    expect(recordActionPath("a b/c", "dismiss")).toBe("/api/v1/records/a%20b%2Fc/dismiss");
  });
});

describe("the destination check gates a commit-attempted row on the page", () => {
  it("disables both buttons until the unticked box is ticked, and again once it is cleared", async () => {
    const stub = createFetchStub(routes([sampleRow()]));
    const view = renderApp("/needs-help");
    await settle();

    const box = checkbox(view.container);
    expect(box.checked).toBe(false);
    expect(view.container.querySelector(".destination-check-label")?.textContent).toBe(
      DESTINATION_CHECK_LABEL,
    );
    // The reason for the box is next to it, in one sentence.
    expect(
      view.container.querySelector(".destination-check-why")?.textContent?.trim(),
    ).not.toBe("");
    expect(button(view.container, ".record-requeue").disabled).toBe(true);
    expect(button(view.container, ".record-dismiss").disabled).toBe(true);

    fireEvent.click(box);
    await settle();
    expect(checkbox(view.container).checked).toBe(true);
    expect(button(view.container, ".record-requeue").disabled).toBe(false);
    expect(button(view.container, ".record-dismiss").disabled).toBe(false);

    fireEvent.click(checkbox(view.container));
    await settle();
    expect(button(view.container, ".record-requeue").disabled).toBe(true);
    expect(button(view.container, ".record-dismiss").disabled).toBe(true);
    stub.restore();
    view.unmount();
  });

  it("asks nothing extra of a row that never reached a commit", async () => {
    const stub = createFetchStub(
      routes([sampleRow({ state: "failed", commit_attempted: false })], {
        "/api/v1/records/record-1/dismiss": { body: { record_id: "record-1", state: "skipped" } },
      }),
    );
    const view = renderApp("/needs-help");
    await settle();

    expect(view.container.querySelector(".destination-check-box")).toBeNull();
    const dismiss = button(view.container, ".record-dismiss");
    expect(dismiss.disabled).toBe(false);
    fireEvent.click(dismiss);
    await settle();

    const sent = posts(stub, "/api/v1/records/record-1/dismiss");
    expect(sent).toHaveLength(1);
    expect(sent[0]?.body).toEqual({});
    stub.restore();
    view.unmount();
  });

  it("sends destination_checked_by only after the tick, and ticks nothing by itself", async () => {
    const stub = createFetchStub(
      routes([sampleRow()], {
        "/api/v1/records/record-1/dismiss": { body: { record_id: "record-1", state: "skipped" } },
      }),
    );
    const view = renderApp("/needs-help");
    await settle();

    // Nothing is ticked on the page's own account.
    expect(checkbox(view.container).checked).toBe(false);
    fireEvent.click(button(view.container, ".record-dismiss"));
    await settle();
    expect(posts(stub, "/api/v1/records/record-1/dismiss")).toHaveLength(0);

    fireEvent.click(checkbox(view.container));
    await settle();
    expect(button(view.container, ".record-dismiss").disabled).toBe(false);
    fireEvent.click(button(view.container, ".record-dismiss"));
    await settle();

    const sent = posts(stub, "/api/v1/records/record-1/dismiss");
    expect(sent).toHaveLength(1);
    expect(sent[0]?.body).toEqual({ destination_checked_by: OPERATOR_ID });
    stub.restore();
    view.unmount();
  });
});

describe("a refused request shows one plain sentence next to the button", () => {
  it("shows a 409 from Dismiss beside the Dismiss button and does not retry", async () => {
    const stub = createFetchStub(
      routes([sampleRow({ commit_attempted: false })], {
        "/api/v1/records/record-1/dismiss": { status: 409, body: { detail: REAL_CONFLICT } },
      }),
    );
    const view = renderApp("/needs-help");
    await settle();

    fireEvent.click(button(view.container, ".record-dismiss"));
    await settle();

    const cell = view.container.querySelector<HTMLElement>(".row-actions");
    const error = cell?.querySelector<HTMLElement>(".action-error");
    expect(error?.textContent).toBe(DEALT_WITH);
    // "next to the button": the same cell the Dismiss button lives in.
    expect(button(view.container, ".record-dismiss").closest(".row-actions")).toBe(cell);
    // The API's own words name a state and a table row, and a person never reads either.
    expect(view.container.textContent).not.toContain("run_record");
    expect(view.container.textContent).not.toContain("parked");
    expect(view.container.textContent).not.toContain("3f0e1c2a");

    expect(callsTo(stub, "/api/v1/records/record-1/dismiss")).toBe(1);
    await settle();
    expect(callsTo(stub, "/api/v1/records/record-1/dismiss")).toBe(1);
    stub.restore();
    view.unmount();
  });

  it("shows a 409 from Try again beside the Try again button, in the same plain words", async () => {
    const stub = createFetchStub(
      routes([sampleRow({ commit_attempted: false })], {
        "/api/v1/records/record-1/requeue": { status: 409, body: { detail: TRIGGER_CONFLICT } },
      }),
    );
    const view = renderApp("/needs-help");
    await settle();

    fireEvent.click(button(view.container, ".record-requeue"));
    await settle();

    const cell = view.container.querySelector<HTMLElement>(".row-actions");
    expect(cell?.querySelector(".action-error")?.textContent).toBe(REFUSAL_FALLBACK);
    expect(button(view.container, ".record-requeue").closest(".row-actions")).toBe(cell);
    expect(view.container.textContent).not.toContain("commit-attempted");
    expect(view.container.textContent).not.toContain("destination check");
    stub.restore();
    view.unmount();
  });
});

describe("the operator identity", () => {
  it("disables every action and says why when the endpoint answers 503", async () => {
    const stub = createFetchStub(
      routes(
        [sampleRow(), sampleRow({ record_id: "record-2", state: "failed", commit_attempted: false })],
        { "/api/v1/operator": { status: 503, body: { detail: OPERATOR_503 } } },
      ),
    );
    const view = renderApp("/needs-help");
    await settle();

    const buttons = view.container.querySelectorAll<HTMLButtonElement>(".record-buttons button");
    expect(buttons.length).toBe(4);
    for (const node of Array.from(buttons)) {
      expect(node.disabled).toBe(true);
    }
    // It refuses to act, and it says why -- including the API's own words and the variable to set.
    const note = view.container.querySelector<HTMLElement>(".operator-note");
    expect(note?.textContent).toContain(OPERATOR_PROBLEM);
    expect(note?.textContent).toContain(OPERATOR_503);
    expect(note?.textContent).toContain("PROCESSLENS_OPERATOR_ID");

    fireEvent.click(button(view.container, ".record-dismiss"));
    await settle();
    expect(posts(stub, "/api/v1/records/record-2/dismiss")).toHaveLength(0);
    stub.restore();
    view.unmount();
  });
});

describe("the page with nothing to show", () => {
  it("says what will appear here in one sentence and offers no primary button", async () => {
    const stub = createFetchStub(routes([]));
    const view = renderApp("/needs-help");
    await settle();

    const note = view.container.querySelector<HTMLElement>(".needs-help .panel-note");
    expect(note?.textContent).toContain("Nothing needs you right now");
    expect(view.container.querySelector(".btn.primary")).toBeNull();
    expect(view.container.querySelector(".destination-check-box")).toBeNull();
    stub.restore();
    view.unmount();
  });
});
