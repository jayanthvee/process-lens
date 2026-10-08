// The attended recipe executor, built on chrome.debugger.
//
// Public entry point: `runRecipe`. Everything else is a seam the tests drive
// directly: the CDP transport, the page-side ladder resolver, the dispatchers,
// the assertion poller, and the run control.
export { chromeDebuggerTransport, attachDebugger, detachDebugger, type CdpTransport } from "./cdp";
export { RunControl } from "./control";
export {
  dispatchClick,
  dispatchFill,
  dispatchNavigate,
  dispatchSelect,
  focusAndSelect,
} from "./dispatch";
export { conditionExpression, conditionTimeout, waitForCondition } from "./assertions";
export { LADDER, TARGET_MARKER, resolveTargetInPage, resolverExpression } from "./ladder";
export { evaluate, markerExpression, resolverCallExpression } from "./page";
export { FrameContexts, readFrameTree, selectFrame } from "./frames";
export {
  assertAllowedOrigin,
  resetTokens,
  resolveValue,
  runRecipe,
  type RunDeps,
  type RunSummary,
} from "./executor";
export {
  ExecutorError,
  type ExecutedAction,
  type ExecutorRecipe,
  type ExecutorStep,
  type RunContext,
  type RunState,
  type RunStatus,
  type StepOutcome,
  type StepReport,
} from "./types";
