/**
 * The commit-attempted confirmation: the one click that can put a record back in front of the
 * ledger's duplicate protection, so it is never one click.
 */

import { describe, expect, it } from "vitest";
import { mountApp } from "../src/app";
import * as logic from "../src/logic";
import * as view from "../src/view";
import type { RecordRow } from "../src/types";
import { OPERATOR_ID, callsTo, createFetchStub, sampleRecord, sampleRunDetail, settle } from "./helpers";

function renderTable(
  container: HTMLElement,
  records: readonly RecordRow[],
  confirmed: Map<string, boolean>,
  operatorId: string | null = OPERATOR_ID,
): void {
  view.clearNode(container);
  container.appendChild(
    view.renderRecordsTable({
      records,
      expanded: new Set<string>(),
      transitionsOf: () => [],
      confirmed,
      operatorId,
      errors: new Map<string, string>(),
      onToggle: () => {},
      onConfirm: (recordId, checked) => {
        confirmed.set(recordId, checked);
      },
      onAction: () => {},
    }),
  );
}

function buttonWith(container: HTMLElement, selector: string): HTMLButtonElement {
  const node = container.querySelector<HTMLButtonElement>(selector);
  if (node === null) throw new Error(`no button matching ${selector}`);
  return node;
}

function checkbox(container: HTMLElement): HTMLInputElement {
  const node = container.querySelector<HTMLInputElement>(".destination-check-box");
  if (node === null) throw new Error("no destination-check checkbox");
  return node;
}

function tick(box: HTMLInputElement, checked: boolean): void {
  box.checked = checked;
  box.dispatchEvent(new Event("change"));
}

describe("the destination check gates a commit-attempted record", () => {
  it("disables both buttons until the unticked confirmation is ticked, and again once it is cleared", () => {
    const record = sampleRecord({ state: "parked", commit_attempted: true });
    const confirmed = new Map<string, boolean>();
    const container = document.createElement("div");
    document.body.appendChild(container);

    renderTable(container, [record], confirmed);
    const first = checkbox(container);
    expect(first.checked).toBe(false);
    expect(container.querySelector(".destination-check-label")?.textContent).toBe(
      logic.DESTINATION_CHECK_LABEL,
    );
    expect(buttonWith(container, ".record-requeue").disabled).toBe(true);
    expect(buttonWith(container, ".record-dismiss").disabled).toBe(true);

    tick(first, true);
    renderTable(container, [record], confirmed);
    expect(checkbox(container).checked).toBe(true);
    expect(buttonWith(container, ".record-requeue").disabled).toBe(false);
    expect(buttonWith(container, ".record-dismiss").disabled).toBe(false);

    tick(checkbox(container), false);
    renderTable(container, [record], confirmed);
    expect(buttonWith(container, ".record-requeue").disabled).toBe(true);
    expect(buttonWith(container, ".record-dismiss").disabled).toBe(true);
  });

  it("asks nothing extra of a record that never reached a commit", () => {
    const record = sampleRecord({ state: "failed", commit_attempted: false });
    const container = document.createElement("div");
    document.body.appendChild(container);

    renderTable(container, [record], new Map<string, boolean>());
    expect(container.querySelector(".destination-check-box")).toBeNull();
    expect(buttonWith(container, ".record-requeue").disabled).toBe(false);
    expect(buttonWith(container, ".record-dismiss").disabled).toBe(false);
  });

  it("stays disabled while the API has no operator id to send", () => {
    const record = sampleRecord({ state: "parked", commit_attempted: true });
    const confirmed = new Map<string, boolean>([["record-1", true]]);
    const container = document.createElement("div");
    document.body.appendChild(container);

    renderTable(container, [record], confirmed, null);
    expect(buttonWith(container, ".record-requeue").disabled).toBe(true);
    expect(buttonWith(container, ".record-dismiss").disabled).toBe(true);
  });

  it("sends destination_checked_by only after the tick, and ticks nothing by itself", async () => {
    const record = sampleRecord({ state: "parked", commit_attempted: true });
    const { fetchImpl, calls } = createFetchStub((call) => {
      if (call.path === "/api/v1/operator") return { status: 200, body: { operator_id: OPERATOR_ID } };
      if (call.path === "/api/v1/runs/run-1") return { status: 200, body: sampleRunDetail() };
      if (call.path === "/api/v1/runs/run-1/records") return { status: 200, body: [record] };
      if (call.path === "/api/v1/runs/run-1/approvals") return { status: 200, body: [] };
      if (call.path.startsWith("/api/v1/runs/run-1/transitions")) return { status: 200, body: [] };
      if (call.path === "/api/v1/records/record-1/dismiss") {
        return { status: 200, body: { record_id: "record-1", state: "skipped" } };
      }
      return { status: 404, body: { detail: `unexpected ${call.method} ${call.path}` } };
    });

    const root = document.createElement("div");
    document.body.appendChild(root);
    const app = mountApp(root, { fetchImpl });
    await app.openRun("run-1");

    const dismiss = root.querySelector<HTMLButtonElement>(".record-dismiss");
    expect(dismiss).not.toBeNull();
    expect(dismiss?.disabled).toBe(true);

    dismiss?.click();
    await settle();
    expect(callsTo(calls, "POST", "/api/v1/records/record-1/dismiss")).toHaveLength(0);

    const box = root.querySelector<HTMLInputElement>(".destination-check-box");
    expect(box).not.toBeNull();
    if (box === null) return;
    tick(box, true);

    const enabled = root.querySelector<HTMLButtonElement>(".record-dismiss");
    expect(enabled?.disabled).toBe(false);
    enabled?.click();
    await settle();

    const posts = callsTo(calls, "POST", "/api/v1/records/record-1/dismiss");
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toEqual({ destination_checked_by: OPERATOR_ID });
    app.destroy();
  });
});

describe("the gate itself", () => {
  it("names both requirements and no more", () => {
    const gate = logic.actionGate({
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

  it("leaves a verified record alone", () => {
    const gate = logic.actionGate({
      state: "verified",
      commitAttempted: true,
      confirmed: true,
      operatorId: OPERATOR_ID,
    });
    expect(gate.shown).toBe(false);
    expect(logic.recordActions("verified")).toEqual({ requeue: false, dismiss: false });
  });
});

describe("the request body", () => {
  it("carries the operator id only when a commit was attempted and the check was confirmed", () => {
    expect(
      logic.actionBody({
        commitAttempted: false,
        confirmed: false,
        operatorId: OPERATOR_ID,
      }),
    ).toEqual({});
    expect(
      logic.actionBody({ commitAttempted: true, confirmed: true, operatorId: OPERATOR_ID }),
    ).toEqual({ destination_checked_by: OPERATOR_ID });
    expect(() =>
      logic.actionBody({ commitAttempted: true, confirmed: false, operatorId: OPERATOR_ID }),
    ).toThrow();
    expect(() =>
      logic.actionBody({ commitAttempted: true, confirmed: true, operatorId: null }),
    ).toThrow();
  });
});
