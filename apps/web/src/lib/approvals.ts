/**
 * The approval decision, in plain words.
 *
 * A run stops at a gate and asks a person to look at the exact values it is about to save. The
 * request's `shown` payload is written for the machine: it carries the step's id, the action's name
 * and the whole target ladder, including selectors. None of that is a person's business
 * (DESIGN.md §2), so this module keeps only the two things the person is actually deciding about --
 * the value and the row's own columns -- and names the decision request is sent as.
 */

import type { ApprovalDecisionBody } from "../types";

/** One line of what the person is being shown. */
export interface ShownLine {
  label: string;
  value: string;
}

/** The keys of `shown` that are engine internals and never reach the screen. */
const INTERNAL_KEYS = new Set(["step", "action", "targets", "within", "frame", "id"]);

/** "email_address" -> "Email address", so a spreadsheet column reads as words. */
function columnName(key: string): string {
  const spaced = key.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  if (spaced === "") return key;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** A value a person can read. An object or an array is summarised, never dumped as JSON. */
function readable(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value.trim() === "" ? null : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const parts = value.map(readable).filter((part): part is string => part !== null);
    return parts.length === 0 ? null : parts.join(", ");
  }
  return null;
}

/**
 * What the person is shown, in order: the value the task is about to save, then the row's own
 * columns. Everything the machine needs and a person does not -- the step's id, the action, the
 * target ladder -- is left out.
 */
export function approvalLines(shown: Record<string, unknown> | null | undefined): ShownLine[] {
  if (shown === null || shown === undefined) return [];
  const lines: ShownLine[] = [];
  const value = readable(shown["value"]);
  if (value !== null) lines.push({ label: "The value it will save", value });

  const record = shown["record"];
  if (record !== null && typeof record === "object" && !Array.isArray(record)) {
    for (const key of Object.keys(record as Record<string, unknown>).sort()) {
      const entry = readable((record as Record<string, unknown>)[key]);
      if (entry !== null) lines.push({ label: columnName(key), value: entry });
    }
  }

  // Anything else the API sends, as long as it is not an engine internal and is readable.
  for (const key of Object.keys(shown).sort()) {
    if (key === "value" || key === "record" || INTERNAL_KEYS.has(key)) continue;
    const entry = readable(shown[key]);
    if (entry !== null) lines.push({ label: columnName(key), value: entry });
  }
  return lines;
}

/** The path the API already serves for a decision. */
export const APPROVALS_PATH = "/api/v1/approvals";

/**
 * The body of a decision. The request id is the exact request the person was shown, and
 * `decided_by` is never invented: without an operator identity there is nobody to record, so this
 * refuses rather than sending a decision the ledger could not attribute.
 */
export function approvalBody(input: {
  requestId: string;
  decision: "approved" | "rejected";
  operatorId: string | null;
}): ApprovalDecisionBody {
  if (input.operatorId === null || input.operatorId === "") {
    throw new Error("Answering an approval needs the API's operator identity.");
  }
  return {
    request_id: input.requestId,
    decision: input.decision,
    decided_by: input.operatorId,
  };
}
