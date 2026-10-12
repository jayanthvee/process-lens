/**
 * The DOM layer, and nothing else. Every value in it — including anything that came out of a user's
 * CSV — goes in through `textContent`. There is no `innerHTML` in this file, by design.
 */

import * as logic from "./logic";
import type { ApprovalDecision, ApprovalRequest, RecordRow, RunDetail, RunSummary, Transition } from "./types";

export interface ElementOptions {
  className?: string;
  text?: string;
  /** Static attribute names and values this module owns; never a value from the API. */
  attrs?: Record<string, string>;
}

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  options: ElementOptions = {},
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (options.className !== undefined) node.className = options.className;
  if (options.text !== undefined) node.textContent = options.text;
  if (options.attrs !== undefined) {
    for (const [name, value] of Object.entries(options.attrs)) node.setAttribute(name, value);
  }
  return node;
}

export function clearNode(node: HTMLElement): void {
  while (node.firstChild !== null) node.removeChild(node.firstChild);
}

export interface ButtonOptions {
  className?: string;
  disabled?: boolean;
  onClick?: () => void;
}

export function button(label: string, options: ButtonOptions = {}): HTMLButtonElement {
  const node = el("button", { className: options.className ?? "action", text: label });
  node.type = "button";
  node.disabled = options.disabled === true;
  if (options.onClick !== undefined) node.addEventListener("click", options.onClick);
  return node;
}

/** The API's `detail` text, rendered right where the button that failed is. */
export function errorSpan(text: string | undefined): HTMLSpanElement | null {
  if (text === undefined || text === "") return null;
  return el("span", { className: "action-error", text, attrs: { role: "alert" } });
}

export function describeValue(value: unknown): string {
  if (value === undefined) return "—";
  if (value === null) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") {
    return String(value);
  }
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

function textOr(value: string | null | undefined, fallback: string): string {
  if (value === null || value === undefined) return fallback;
  const trimmed = value.trim();
  return trimmed === "" ? fallback : value;
}

function countsLine(counts: Record<string, number>): string {
  const entries = logic.countEntries(counts);
  if (entries.length === 0) return "—";
  return entries.map((entry) => `${entry.label.toLowerCase()} ${entry.value}`).join(" · ");
}

function recipeLabel(run: RunSummary): string {
  const name = run.recipe_name === null ? "" : run.recipe_name;
  const version = run.recipe_version === null ? "" : run.recipe_version;
  if (name === "" && version === "") return "—";
  return version === "" ? name : `${name} v${version}`;
}

/** The runs list. Every row links to its run. */
export function renderRunsList(runs: readonly RunSummary[]): HTMLElement {
  const section = el("section", { className: "runs" });
  section.appendChild(el("h2", { text: "Runs" }));
  if (runs.length === 0) {
    section.appendChild(el("p", { className: "muted", text: "No runs yet." }));
    return section;
  }
  const table = el("table", { className: "runs-table" });
  const head = el("tr");
  for (const label of ["Run", "Status", "Pause reason", "Recipe", "Created", "Counts"]) {
    head.appendChild(el("th", { text: label }));
  }
  const thead = el("thead");
  thead.appendChild(head);
  table.appendChild(thead);
  const tbody = el("tbody");
  for (const run of runs) {
    const row = el("tr", { className: "run-row" });
    const idCell = el("td", { className: "run-id" });
    idCell.appendChild(el("a", { text: run.id, attrs: { href: logic.runHash(run.id) } }));
    row.appendChild(idCell);
    row.appendChild(el("td", { className: "run-status", text: logic.humanizeStatus(run.status) }));
    row.appendChild(
      el("td", { className: "run-pause-reason", text: logic.humanizePauseReason(run.pause_reason) ?? "—" }),
    );
    row.appendChild(el("td", { className: "run-recipe", text: recipeLabel(run) }));
    row.appendChild(el("td", { className: "run-created", text: textOr(run.created_at, "—") }));
    row.appendChild(el("td", { className: "run-counts", text: countsLine(run.counts) }));
    tbody.appendChild(row);
  }
  table.appendChild(tbody);
  section.appendChild(table);
  return section;
}

export interface RunHeaderOptions {
  run: RunDetail;
  errors: ReadonlyMap<string, string>;
  onAction: (action: logic.RunAction) => void;
}

/** Status, pause reason in plain words, counts, the buttons the status allows, and results.csv. */
export function renderRunHeader(options: RunHeaderOptions): HTMLElement {
  const run = options.run;
  const section = el("section", { className: "run-header" });
  section.appendChild(el("h2", { className: "run-title", text: `Run ${run.id}` }));

  const statusLine = el("p", { className: "run-status-line" });
  statusLine.appendChild(el("span", { className: `run-status run-status-${run.status}`, text: logic.humanizeStatus(run.status) }));
  section.appendChild(statusLine);

  const pauseText = logic.humanizePauseReason(run.pause_reason);
  section.appendChild(
    el("p", {
      className: pauseText === null ? "pause-reason muted" : "pause-reason",
      text: pauseText ?? "Not paused.",
    }),
  );

  const counts = el("ul", { className: "counts" });
  for (const entry of logic.countEntries(run.counts)) {
    const item = el("li", { className: `count count-${entry.key}` });
    item.appendChild(el("span", { className: "count-label", text: `${entry.label}: ` }));
    item.appendChild(el("span", { className: "count-value", text: String(entry.value) }));
    counts.appendChild(item);
  }
  section.appendChild(counts);

  const availability = logic.runActions(run.status);
  const actionBar = el("p", { className: "run-actions" });
  for (const action of ["pause", "resume", "cancel"] as const) {
    if (availability[action] !== true) continue;
    actionBar.appendChild(
      button(logic.runActionLabel(action), {
        className: `run-action run-action-${action}`,
        onClick: () => options.onAction(action),
      }),
    );
    const span = errorSpan(options.errors.get(`run:${action}`));
    if (span !== null) actionBar.appendChild(span);
  }
  if (actionBar.childElementCount > 0) section.appendChild(actionBar);

  const resultsLine = el("p", { className: "results" });
  resultsLine.appendChild(
    el("a", {
      className: "results-link",
      text: "Download results.csv",
      attrs: { href: logic.resultsCsvPath(run.id) },
    }),
  );
  section.appendChild(resultsLine);
  return section;
}

export interface RecordsTableOptions {
  records: readonly RecordRow[];
  expanded: ReadonlySet<string>;
  transitionsOf: (recordId: string) => readonly Transition[];
  confirmed: ReadonlyMap<string, boolean>;
  operatorId: string | null;
  errors: ReadonlyMap<string, string>;
  onToggle: (recordId: string) => void;
  onConfirm: (recordId: string, checked: boolean) => void;
  onAction: (recordId: string, action: logic.RecordAction) => void;
}

const RECORD_COLUMNS = 5;

/** Row key, state, reason, attempts — and a row that expands to show its transitions. */
export function renderRecordsTable(options: RecordsTableOptions): HTMLElement {
  const section = el("section", { className: "records" });
  section.appendChild(el("h2", { text: "Records" }));
  if (options.records.length === 0) {
    section.appendChild(el("p", { className: "muted", text: "This run has no records yet." }));
    return section;
  }
  const table = el("table", { className: "records-table" });
  const head = el("tr");
  for (const label of ["Record", "State", "Reason", "Attempts", "Actions"]) {
    head.appendChild(el("th", { text: label }));
  }
  const thead = el("thead");
  thead.appendChild(head);
  table.appendChild(thead);
  const tbody = el("tbody");
  for (const record of options.records) {
    tbody.appendChild(renderRecordRow(record, options));
    const transitionRow = renderTransitionsRow(record, options);
    if (transitionRow !== null) tbody.appendChild(transitionRow);
  }
  table.appendChild(tbody);
  section.appendChild(table);
  return section;
}

function renderRecordRow(record: RecordRow, options: RecordsTableOptions): HTMLTableRowElement {
  const gate = logic.actionGate({
    state: record.state,
    commitAttempted: record.commit_attempted,
    confirmed: options.confirmed.get(record.id) === true,
    operatorId: options.operatorId,
  });
  const row = el("tr", { className: `record record-${record.state}`, attrs: { "data-record-id": record.id } });

  const keyCell = el("td", { className: "record-key" });
  const isExpanded = options.expanded.has(record.id);
  keyCell.appendChild(
    button(isExpanded ? "Hide transitions" : "Transitions", {
      className: "record-toggle",
      onClick: () => options.onToggle(record.id),
    }),
  );
  // A record key comes from the operator's CSV: untrusted text, so textContent only.
  keyCell.appendChild(el("span", { className: "record-key-value", text: record.record_key }));
  row.appendChild(keyCell);

  row.appendChild(el("td", { className: "record-state", text: logic.humanizeStatus(record.state) }));
  row.appendChild(el("td", { className: "record-reason", text: textOr(record.reason, "—") }));
  row.appendChild(
    el("td", { className: "record-attempts", text: `${record.attempts} / ${record.max_attempts}` }),
  );

  const actions = el("td", { className: "record-actions" });
  if (gate.shown === false) {
    actions.appendChild(el("span", { className: "muted", text: "No action" }));
  } else {
    if (gate.needsConfirmation) {
      const label = el("label", { className: "destination-check" });
      const box = el("input", { className: "destination-check-box" });
      box.type = "checkbox";
      // Never pre-ticked: this is the one click that can bypass the duplicate protection.
      box.checked = options.confirmed.get(record.id) === true;
      box.addEventListener("change", () => options.onConfirm(record.id, box.checked));
      label.appendChild(box);
      label.appendChild(el("span", { className: "destination-check-label", text: logic.DESTINATION_CHECK_LABEL }));
      actions.appendChild(label);
    }
    actions.appendChild(
      button("Re-queue", {
        className: "record-requeue",
        disabled: gate.disabled,
        onClick: () => options.onAction(record.id, "requeue"),
      }),
    );
    actions.appendChild(
      button("Dismiss", {
        className: "record-dismiss",
        disabled: gate.disabled,
        onClick: () => options.onAction(record.id, "dismiss"),
      }),
    );
    for (const action of ["requeue", "dismiss"] as const) {
      const span = errorSpan(options.errors.get(`${record.id}:${action}`));
      if (span !== null) actions.appendChild(span);
    }
  }
  row.appendChild(actions);

  return row;
}

/** The second row an expanded record shows: its transitions, or a plain sentence when there are none. */
function renderTransitionsRow(
  record: RecordRow,
  options: RecordsTableOptions,
): HTMLTableRowElement | null {
  if (options.expanded.has(record.id) === false) return null;
  const detailRow = el("tr", { className: "record-transitions-row" });
  const cell = el("td", { className: "record-transitions", attrs: { colspan: String(RECORD_COLUMNS) } });
  const transitions = options.transitionsOf(record.id);
  if (transitions.length === 0) {
    cell.appendChild(el("p", { className: "muted", text: "No transitions recorded yet." }));
  } else {
    const inner = el("table", { className: "transitions-table" });
    const innerHead = el("tr");
    for (const label of ["From", "To", "Reason", "Actor", "At"]) {
      innerHead.appendChild(el("th", { text: label }));
    }
    const innerThead = el("thead");
    innerThead.appendChild(innerHead);
    inner.appendChild(innerThead);
    const innerBody = el("tbody");
    for (const transition of transitions) {
      const line = el("tr", { className: "transition" });
      line.appendChild(el("td", { text: textOr(transition.from_state, "—") }));
      line.appendChild(el("td", { text: transition.to_state }));
      line.appendChild(el("td", { text: textOr(transition.reason, "—") }));
      line.appendChild(el("td", { text: textOr(transition.actor, "—") }));
      line.appendChild(el("td", { text: transition.at }));
      innerBody.appendChild(line);
    }
    inner.appendChild(innerBody);
    cell.appendChild(inner);
  }
  detailRow.appendChild(cell);
  return detailRow;
}

export interface ApprovalsOptions {
  approvals: readonly ApprovalRequest[];
  operatorId: string | null;
  errors: ReadonlyMap<string, string>;
  onDecision: (requestId: string, decision: ApprovalDecision) => void;
}

/** Each open request, with Approve and Reject. */
export function renderApprovals(options: ApprovalsOptions): HTMLElement {
  const section = el("section", { className: "approvals" });
  section.appendChild(el("h2", { text: "Approvals" }));
  if (options.approvals.length === 0) {
    section.appendChild(el("p", { className: "muted", text: "No open approval requests." }));
    return section;
  }
  for (const approval of options.approvals) {
    const card = el("article", { className: "approval", attrs: { "data-approval-id": approval.id } });
    card.appendChild(el("h3", { text: `Approval ${approval.id}` }));
    card.appendChild(
      el("p", {
        className: "approval-what",
        text: `${logic.humanizeToken(approval.kind)} · step ${approval.step_id} · record ${approval.run_record_id}`,
      }),
    );
    const values = el("ul", { className: "approval-values" });
    const shown = approval.shown ?? {};
    for (const [key, value] of Object.entries(shown)) {
      values.appendChild(el("li", { text: `${key}: ${describeValue(value)}` }));
    }
    card.appendChild(values);

    const bar = el("p", { className: "approval-actions" });
    for (const decision of ["approved", "rejected"] as const) {
      const label = decision === "approved" ? "Approve" : "Reject";
      bar.appendChild(
        button(label, {
          className: `approval-decision approval-${decision}`,
          disabled: options.operatorId === null,
          onClick: () => options.onDecision(approval.id, decision),
        }),
      );
      const span = errorSpan(options.errors.get(`approval:${approval.id}:${decision}`));
      if (span !== null) bar.appendChild(span);
    }
    if (options.operatorId === null) {
      bar.appendChild(
        el("span", {
          className: "muted operator-note",
          text: "Disabled: answering an approval has to record who decided, and PROCESSLENS_OPERATOR_ID is not set.",
        }),
      );
    }
    card.appendChild(bar);
    section.appendChild(card);
  }
  return section;
}
