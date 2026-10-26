// The attended recipe executor, built on chrome.debugger.
//
// Public entry point: `runRecipe`. Everything else is a seam the tests drive
// directly: the CDP transport, the page-side ladder resolver, the dispatchers,
// the assertion poller, and the run control.
export { chromeDebuggerTransport, attachDebugger, detachDebugger, onDebuggerDetach, type CdpTransport } from "./cdp";
export { RunControl } from "./control";
export {
  dispatchClick,
  dispatchFill,
  dispatchNavigate,
  dispatchSelect,
  focusAndSelect,
  measureTarget,
  prepareField,
} from "./dispatch";
export { conditionExpression, conditionTimeout, waitForCondition } from "./assertions";
export {
  DEFAULT_DISPATCH_PATH,
  IN_PAGE_DISPATCH_KEY,
  dispatchPathFrom,
  inPageDispatchEnabled,
  type DispatchPath,
} from "./flags";
export {
  dialogOverrideExpression,
  inPageClick,
  inPageFill,
  inPagePress,
  inPageSelect,
  inPageUpload,
  type InPageFile,
  type InPageSelectAnswer,
} from "./inpage";
export {
  LADDER,
  TARGET_MARKER,
  measureExpression,
  measureTargetInPage,
  resolveTargetInPage,
  resolverExpression,
} from "./ladder";
export { evaluate, markerExpression, resolverCallExpression } from "./page";
export { FrameContexts, readFrameTree, selectFrame } from "./frames";
export {
  assertAllowedOrigin,
  resetTokens,
  resolveValue,
  runRecipe,
  validateRecipe,
  type RunDeps,
  type RunSummary,
} from "./executor";
export {
  ExecutorError,
  type ExecutedAction,
  type ExecutorRecipe,
  type ExecutorStep,
  type FailureDisposition,
  type RunContext,
  type RunState,
  type RunStatus,
  type StepOutcome,
  type StepReport,
} from "./types";
