/** Test-only helpers: a fake `fetch`, recorded calls, and the fixtures the API would send. */

import { vi } from "vitest";

import type { ApprovalRequest, RecordRow, RunDetail, RunSummary, Transition } from "../src/types";

export const OPERATOR_ID = "3f1c4f5e-0000-4000-8000-00000000abcd";

export interface RecordedCall {
  method: string;
  path: string;
  body: unknown;
}

export interface StubResult {
  status: number;
  body: unknown;
}

export interface FetchStub {
  fetchImpl: typeof fetch;
  calls: RecordedCall[];
}

/** A `Response` good enough for the client: status, ok, and a JSON text body. */
export function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => (body === undefined ? "" : JSON.stringify(body)),
  } as unknown as Response;
}

export function createFetchStub(
  handler: (call: RecordedCall) => StubResult | Promise<StubResult>,
): FetchStub {
  const calls: RecordedCall[] = [];
  const fetchImpl = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const rawBody = init?.body;
    const call: RecordedCall = {
      method: init?.method ?? "GET",
      path: typeof input === "string" ? input : String(input),
      body: typeof rawBody === "string" ? JSON.parse(rawBody) : undefined,
    };
    calls.push(call);
    const result = await handler(call);
    return jsonResponse(result.status, result.body);
  };
  return { fetchImpl: fetchImpl as unknown as typeof fetch, calls };
}

/** Every call that hit `method` on a path containing `fragment`. */
export function callsTo(calls: readonly RecordedCall[], method: string, fragment: string): RecordedCall[] {
  return calls.filter((call) => call.method === method && call.path.includes(fragment));
}

export function sampleRecord(overrides: Partial<RecordRow> = {}): RecordRow {
  return {
    id: "record-1",
    record_key: "ACME-1",
    state: "parked",
    reason: "needs a person",
    attempts: 1,
    max_attempts: 3,
    commit_attempted: false,
    destination_ref: null,
    ...overrides,
  };
}

export function sampleRunDetail(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    id: "run-1",
    status: "running",
    pause_reason: null,
    counts: { total: 2, verified: 1, skipped: 0, parked: 1, failed: 0, in_progress: 0 },
    open_approval: null,
    ...overrides,
  };
}

export function sampleRunSummary(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    id: "run-1",
    status: "running",
    pause_reason: null,
    created_at: "2026-01-01T00:00:00Z",
    recipe_name: "Enrolment recipe",
    recipe_version: "v3",
    counts: { verified: 1, parked: 1 },
    ...overrides,
  };
}

export function sampleApproval(overrides: Partial<ApprovalRequest> = {}): ApprovalRequest {
  return {
    id: "approval-1",
    run_record_id: "record-1",
    step_id: "step-7",
    kind: "low_confidence",
    shown: { field: "Name", value: "ACME" },
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function sampleTransition(overrides: Partial<Transition> = {}): Transition {
  return {
    id: 1,
    run_record_id: "record-1",
    from_state: "in_progress",
    to_state: "parked",
    reason: "no match",
    actor: "worker",
    at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

/** Let the promise chains a click or a tick starts finish.
 *
 * Under a fake clock a real ``setTimeout`` never fires, so a caller that installed one (the polling
 * tests do) would wait for a wall-clock tick that can never come and time out. When fake timers are
 * active this drains microtasks and lets the faked timers run instead; otherwise it yields to the
 * real event loop a few times.
 */
export async function settle(): Promise<void> {
  if (vi.isFakeTimers()) {
    await vi.advanceTimersByTimeAsync(0);
    for (let i = 0; i < 6; i += 1) {
      await Promise.resolve();
    }
    return;
  }
  for (let i = 0; i < 6; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}
