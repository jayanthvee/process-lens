/**
 * The page's wiring: read the API, keep the state, render it, and do exactly the action a person
 * clicked — never a retry of its own.
 *
 * The fetch implementation, the poll interval and the timers are all injectable, so the tests drive
 * real behaviour instead of scraping the DOM.
 */

import { ApiClient, operatorProblemText, readOperatorId } from "./api";
import * as logic from "./logic";
import * as view from "./view";
import type {
  ApprovalDecision,
  ApprovalDecisionBody,
  ApprovalRequest,
  OperatorInfo,
  RecordActionBody,
  RecordActionResponse,
  RecordRow,
  RunDetail,
  RunSummary,
  Transition,
} from "./types";

export interface MountOptions {
  fetchImpl?: typeof fetch;
  intervalMs?: number;
  initialHash?: string;
  timers?: logic.Timers;
  listenToHashChange?: boolean;
}

export interface AppStatus {
  route: logic.Route;
  runId: string | null;
  runStatus: string | null;
  polling: boolean;
  operatorId: string | null;
  operatorProblem: string | null;
}

export interface AppHandle {
  refresh: () => Promise<void>;
  showRuns: () => Promise<void>;
  openRun: (runId: string) => Promise<void>;
  render: () => void;
  status: () => AppStatus;
  destroy: () => void;
}

interface AppState {
  route: logic.Route;
  operatorChecked: boolean;
  operatorId: string | null;
  operatorProblem: string | null;
  runs: RunSummary[];
  run: RunDetail | null;
  records: RecordRow[];
  transitions: Map<string, Transition[]>;
  approvals: ApprovalRequest[];
  expanded: Set<string>;
  confirmed: Map<string, boolean>;
  errors: Map<string, string>;
  pageError: string | null;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function mountApp(root: HTMLElement, options: MountOptions = {}): AppHandle {
  const api = new ApiClient(options.fetchImpl === undefined ? {} : { fetchImpl: options.fetchImpl });
  const intervalMs = options.intervalMs ?? logic.POLL_INTERVAL_MS;
  const timers = options.timers ?? logic.DEFAULT_TIMERS;

  const state: AppState = {
    route: logic.routeFromHash(options.initialHash ?? ""),
    operatorChecked: false,
    operatorId: null,
    operatorProblem: null,
    runs: [],
    run: null,
    records: [],
    transitions: new Map<string, Transition[]>(),
    approvals: [],
    expanded: new Set<string>(),
    confirmed: new Map<string, boolean>(),
    errors: new Map<string, string>(),
    pageError: null,
  };

  let destroyed = false;

  const poller = logic.createPoller({
    intervalMs,
    timers,
    onTick: () => {
      void refresh();
    },
    shouldContinue: () => state.run !== null && logic.shouldPoll(state.run.status),
  });

  function syncPoller(): void {
    if (destroyed === false && state.run !== null && logic.shouldPoll(state.run.status)) {
      poller.start();
    } else {
      poller.stop();
    }
  }

  async function loadOperator(): Promise<void> {
    const result = await api.get<OperatorInfo>(logic.operatorPath());
    state.operatorChecked = true;
    const id = readOperatorId(result);
    if (id !== null) {
      state.operatorId = id;
      state.operatorProblem = null;
      return;
    }
    state.operatorId = null;
    state.operatorProblem = operatorProblemText(result) ?? logic.OPERATOR_UNAVAILABLE_MESSAGE;
  }

  async function loadRuns(): Promise<void> {
    const result = await api.get<RunSummary[]>(logic.runsPath());
    if (result.ok === false) {
      state.runs = [];
      state.pageError = result.detail;
      return;
    }
    state.runs = Array.isArray(result.data) ? result.data : [];
  }

  async function loadTransitions(runId: string, reset: boolean): Promise<void> {
    let after = 0;
    if (reset) {
      state.transitions.clear();
    } else {
      for (const list of state.transitions.values()) {
        for (const transition of list) after = Math.max(after, transition.id);
      }
    }
    const result = await api.get<Transition[]>(logic.transitionsPath(runId, after));
    if (result.ok === false || Array.isArray(result.data) === false) return;
    for (const transition of result.data) {
      const existing = state.transitions.get(transition.run_record_id) ?? [];
      existing.push(transition);
      state.transitions.set(transition.run_record_id, existing);
    }
  }

  async function loadRun(runId: string): Promise<void> {
    const detail = await api.get<RunDetail>(logic.runPath(runId));
    if (detail.ok === false) {
      state.run = null;
      state.records = [];
      state.approvals = [];
      state.pageError = detail.detail;
      return;
    }
    state.run = detail.data;
    const [records, approvals] = await Promise.all([
      api.get<RecordRow[]>(logic.recordsPath(runId)),
      api.get<ApprovalRequest[]>(logic.approvalsPath(runId)),
    ]);
    state.records = records.ok === true && Array.isArray(records.data) ? records.data : [];
    state.approvals = approvals.ok === true && Array.isArray(approvals.data) ? approvals.data : [];
    if (state.expanded.size > 0) await loadTransitions(runId, false);
  }

  async function refresh(): Promise<void> {
    if (destroyed) return;
    state.pageError = null;
    if (state.operatorChecked === false) await loadOperator();
    if (state.route.name === "runs") {
      state.run = null;
      await loadRuns();
    } else {
      await loadRun(state.route.id);
    }
    syncPoller();
    render();
  }

  function confirmedFor(recordId: string): boolean {
    return state.confirmed.get(recordId) === true;
  }

  async function runAction(action: logic.RunAction): Promise<void> {
    const run = state.run;
    if (run === null) return;
    const key = `run:${action}`;
    state.errors.delete(key);
    render();
    const result = await api.post<RunDetail>(logic.runActionPath(run.id, action));
    if (result.ok === false) {
      state.errors.set(key, result.detail);
      render();
      return;
    }
    await refresh();
  }

  async function recordAction(recordId: string, action: logic.RecordAction): Promise<void> {
    const record = state.records.find((candidate) => candidate.id === recordId);
    if (record === undefined) return;
    const key = `${recordId}:${action}`;
    state.errors.delete(key);
    const gate = logic.actionGate({
      state: record.state,
      commitAttempted: record.commit_attempted,
      confirmed: confirmedFor(recordId),
      operatorId: state.operatorId,
    });
    if (gate.disabled) {
      render();
      return;
    }
    let body: RecordActionBody;
    try {
      body = logic.actionBody({
        commitAttempted: record.commit_attempted,
        confirmed: confirmedFor(recordId),
        operatorId: state.operatorId,
      });
    } catch (error) {
      state.errors.set(key, errorMessage(error));
      render();
      return;
    }
    const result = await api.post<RecordActionResponse>(logic.recordActionPath(recordId, action), body);
    if (result.ok === false) {
      state.errors.set(key, result.detail);
      render();
      return;
    }
    await refresh();
  }

  async function decide(requestId: string, decision: ApprovalDecision): Promise<void> {
    const key = `approval:${requestId}:${decision}`;
    state.errors.delete(key);
    let body: ApprovalDecisionBody;
    try {
      body = logic.approvalBody({ requestId, decision, operatorId: state.operatorId });
    } catch (error) {
      state.errors.set(key, errorMessage(error));
      render();
      return;
    }
    const result = await api.post(logic.approvalDecisionPath(), body);
    if (result.ok === false) {
      state.errors.set(key, result.detail);
      render();
      return;
    }
    await refresh();
  }

  async function toggleRecord(recordId: string): Promise<void> {
    if (state.expanded.has(recordId)) {
      state.expanded.delete(recordId);
    } else {
      state.expanded.add(recordId);
      if (state.run !== null) await loadTransitions(state.run.id, true);
    }
    render();
  }

  function renderRunSection(run: RunDetail): HTMLElement {
    const section = view.el("section", { className: "run" });
    section.appendChild(
      view.renderRunHeader({
        run,
        errors: state.errors,
        onAction: (action) => {
          void runAction(action);
        },
      }),
    );
    section.appendChild(
      view.renderRecordsTable({
        records: state.records,
        expanded: state.expanded,
        transitionsOf: (recordId) => state.transitions.get(recordId) ?? [],
        confirmed: state.confirmed,
        operatorId: state.operatorId,
        errors: state.errors,
        onToggle: (recordId) => {
          void toggleRecord(recordId);
        },
        onConfirm: (recordId, checked) => {
          state.confirmed.set(recordId, checked);
          render();
        },
        onAction: (recordId, action) => {
          void recordAction(recordId, action);
        },
      }),
    );
    section.appendChild(
      view.renderApprovals({
        approvals: state.approvals,
        operatorId: state.operatorId,
        errors: state.errors,
        onDecision: (requestId, decision) => {
          void decide(requestId, decision);
        },
      }),
    );
    return section;
  }

  function render(): void {
    view.clearNode(root);
    const page = view.el("div", { className: "page" });
    if (state.operatorProblem !== null) {
      page.appendChild(
        view.el("p", {
          className: "operator-problem",
          text: `${logic.OPERATOR_UNAVAILABLE_MESSAGE} The API said: ${state.operatorProblem}`,
          attrs: { role: "status" },
        }),
      );
    }
    if (state.pageError !== null) {
      page.appendChild(
        view.el("p", { className: "page-error", text: state.pageError, attrs: { role: "alert" } }),
      );
    }
    if (state.route.name === "runs") {
      page.appendChild(view.renderRunsList(state.runs));
    } else if (state.run === null) {
      page.appendChild(view.el("p", { className: "muted", text: "Loading the run…" }));
    } else {
      page.appendChild(renderRunSection(state.run));
    }
    root.appendChild(page);
  }

  async function showRuns(): Promise<void> {
    state.route = { name: "runs" };
    state.expanded.clear();
    state.transitions.clear();
    await refresh();
  }

  async function openRun(runId: string): Promise<void> {
    state.route = { name: "run", id: runId };
    state.expanded.clear();
    state.transitions.clear();
    state.confirmed.clear();
    state.errors.clear();
    await refresh();
  }

  const maybeWindow: Window | undefined = typeof window === "undefined" ? undefined : window;
  const onHashChange = (): void => {
    const route = logic.routeFromHash(maybeWindow?.location.hash ?? "");
    if (route.name === "run") {
      void openRun(route.id);
    } else {
      void showRuns();
    }
  };
  if (options.listenToHashChange === true && maybeWindow !== undefined) {
    maybeWindow.addEventListener("hashchange", onHashChange);
  }

  render();

  return {
    refresh,
    showRuns,
    openRun,
    render,
    status: () => ({
      route: state.route,
      runId: state.run === null ? null : state.run.id,
      runStatus: state.run === null ? null : state.run.status,
      polling: poller.isRunning(),
      operatorId: state.operatorId,
      operatorProblem: state.operatorProblem,
    }),
    destroy: () => {
      destroyed = true;
      poller.stop();
      if (options.listenToHashChange === true && maybeWindow !== undefined) {
        maybeWindow.removeEventListener("hashchange", onHashChange);
      }
      view.clearNode(root);
    },
  };
}
