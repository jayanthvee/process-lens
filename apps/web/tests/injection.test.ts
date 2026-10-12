/**
 * Record values come from the operator's CSV, so they are untrusted text. A value that looks like
 * markup must land in the document as characters, never as an element.
 */

import { describe, expect, it } from "vitest";
import { mountApp } from "../src/app";
import * as logic from "../src/logic";
import * as view from "../src/view";
import type { RecordRow } from "../src/types";
import { OPERATOR_ID, createFetchStub, sampleRecord, sampleRunDetail } from "./helpers";

const HOSTILE = '<img src=x onerror=alert(1)>ACME-1';

function table(record: RecordRow): HTMLElement {
  return view.renderRecordsTable({
    records: [record],
    expanded: new Set<string>([record.id]),
    transitionsOf: () => [
      {
        id: 1,
        run_record_id: record.id,
        from_state: "<b>in_progress</b>",
        to_state: "parked",
        reason: HOSTILE,
        actor: HOSTILE,
        at: "2026-01-01T00:00:00Z",
      },
    ],
    confirmed: new Map<string, boolean>(),
    operatorId: OPERATOR_ID,
    errors: new Map<string, string>(),
    onToggle: () => {},
    onConfirm: () => {},
    onAction: () => {},
  });
}

describe("untrusted record values are rendered as text", () => {
  it("shows a record key containing markup as characters, with no element made from it", () => {
    const node = table(sampleRecord({ record_key: HOSTILE, reason: HOSTILE }));

    expect(node.querySelectorAll("img")).toHaveLength(0);
    expect(node.querySelectorAll("script")).toHaveLength(0);
    expect(node.querySelector(".record-key-value")?.textContent).toBe(HOSTILE);
    expect(node.querySelector(".record-reason")?.textContent).toBe(HOSTILE);
    expect(node.querySelector(".record-key-value")?.children).toHaveLength(0);
  });

  it("treats a transition's reason and actor the same way", () => {
    const node = table(sampleRecord({ record_key: "ACME-1" }));

    expect(node.querySelectorAll("img")).toHaveLength(0);
    const cells = Array.from(node.querySelectorAll(".transition td")).map((cell) => cell.textContent);
    expect(cells).toContain(HOSTILE);
  });

  it("never writes markup through the element helper", () => {
    const node = view.el("span", { text: HOSTILE });
    expect(node.querySelector("img")).toBeNull();
    expect(node.textContent).toBe(HOSTILE);
    expect(node.innerHTML).toBe("&lt;img src=x onerror=alert(1)&gt;ACME-1");
  });

  it("keeps the page host element free of markup when the API sends it, and says the count", async () => {
    const hostileRun = sampleRunDetail({ id: "run-1", status: "paused", pause_reason: "challenge" });
    const { fetchImpl } = createFetchStub((call) => {
      if (call.path === "/api/v1/operator") return { status: 200, body: { operator_id: OPERATOR_ID } };
      if (call.path === "/api/v1/runs/run-1") return { status: 200, body: hostileRun };
      if (call.path === "/api/v1/runs/run-1/records") {
        return { status: 200, body: [sampleRecord({ record_key: HOSTILE, state: "parked" })] };
      }
      if (call.path === "/api/v1/runs/run-1/approvals") {
        return {
          status: 200,
          body: [
            {
              id: "approval-1",
              run_record_id: "record-1",
              step_id: "step-7",
              kind: "low_confidence",
              shown: { note: HOSTILE },
              created_at: "2026-01-01T00:00:00Z",
            },
          ],
        };
      }
      return { status: 404, body: { detail: "unexpected" } };
    });

    const root = document.createElement("div");
    document.body.appendChild(root);
    const app = mountApp(root, { fetchImpl });
    await app.openRun("run-1");

    expect(root.querySelectorAll("img")).toHaveLength(0);
    expect(root.textContent).toContain(HOSTILE);
    expect(logic.humanizePauseReason("challenge")).toContain("bound tab");
    app.destroy();
  });
});
