// The per-step work, split out of the run loop (PL-014).
//
// The attended run loop and the command session need the same step mechanics but
// in different shapes. A run performs a step and immediately verifies its
// post-condition; a command session must be able to *prepare* a step (resolve
// and hit-test the target, and send no input) and only later *dispatch* it, when
// the runner says so. Both call the functions here, so there is one
// implementation of resolving, selecting, filling, clicking, and navigating.
//
// `resolveStepTarget` is the prepare half; `performStep` is the dispatch half.
// `performStep` deliberately does not verify `assert_after` ------ the caller owns
// that, because in command mode the runner sends a separate `check`.
import type { ValueSource } from "../shared/recipe-types";
import { disarmAbandonGuard } from "./abandon-guard";
import type { ConditionContext } from "./assertions";
import type { CdpTransport } from "./cdp";
import {
  DEFAULT_NAVIGATE_TIMEOUT_MS,
  dispatchClick,
  dispatchFill,
  dispatchNavigate,
  dispatchPress,
  dispatchSelect,
  measureTarget,
  type InputHooks,
} from "./dispatch";
import { destructiveMatch, isOption } from "./guards";
import { readFrameTree, selectFrame, type FrameContexts } from "./frames";
import {
  resolverExpression,
  TARGET_MARKER,
  type ResolveQuery,
  type ResolveResult,
  type ResolveRung,
} from "./ladder";
import { evaluate, markerExpression, resolverCallExpression } from "./page";
import { ExecutorError, type ExecutorStep, type RunContext, type StepReport } from "./types";

/** The seams a single step needs: a CDP channel, the resolver source, and any frame contexts. */
export interface StepRunDeps {
  cdp: CdpTransport;
  /** The page-side resolver source. Tests may inject a stub. */
  resolverSource?: string;
  /** Execution contexts, for steps that name a frame. */
  frames?: FrameContexts;
  /**
   * True when the step arrived over the PL-014 command wire. The runner then always names the
   * deadline it will wait for, so the extension never falls back to a default of its own for a
   * command-mode step (R4).
   */
  commandMode?: boolean;
  /**
   * The owner's policing of this step's own input. `beforeInput` runs immediately before every
   * trusted input CDP call and may throw, so a withdrawn or expired command is refused at the
   * last moment rather than before the guards and the resolve (F1, B3).
   */
  beforeInput?: () => void;
}

/** A step's target, resolved, hit-tested, and stamped with its marker. */
export interface ResolvedTarget {
  ok: true;
  token: string;
  rung: number;
  by: string;
  tag: string;
  /** The matched element's accessible name, for the destructive-control guard. */
  name: string;
  /** The matched element's role: a picked option is never judged destructive (PL-066). */
  role?: string;
}

/** Why a step's target could not be resolved. */
export interface ResolveFailure {
  ok: false;
  error: string;
}

let tokenCounter = 0;

function nextToken(): string {
  tokenCounter += 1;
  return `pl-target-${tokenCounter}`;
}

/** Reset the marker counter. Only tests need this. */
export function resetTokens(): void {
  tokenCounter = 0;
}

function originOf(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.protocol}//${parsed.host}`;
  } catch {
    return "";
  }
}

/**
 * Resolve a value source to a concrete value. The attended executor resolves
 * the sources that need no run-time services: a constant, an input column, and a
 * page variable. A named value that the row or the run does not carry is a
 * `park`: the record is missing data, and typing an empty string would submit a
 * blank value to the destination.
 */
export function resolveValue(value: ValueSource | undefined, context: RunContext): unknown {
  if (!value) return undefined;
  const record = value as {
    kind?: string;
    value?: unknown;
    column?: string;
    variable?: string;
    field?: string;
  };
  switch (record.kind) {
    case "constant":
      return record.value;
    case "input": {
      const column = record.column ?? "";
      if (!Object.prototype.hasOwnProperty.call(context.row, column)) {
        throw new ExecutorError(
          `the input column ${JSON.stringify(column)} is missing from this record's row`,
          undefined,
          "park",
        );
      }
      const cell = context.row[column];
      if (cell === undefined || cell === null) {
        throw new ExecutorError(
          `the input column ${JSON.stringify(column)} has no value in this record's row`,
          undefined,
          "park",
        );
      }
      return cell;
    }
    case "page": {
      const variable = record.variable ?? "";
      const bound = context.variables;
      if (!bound || !Object.prototype.hasOwnProperty.call(bound, variable)) {
        throw new ExecutorError(
          `the page variable ${JSON.stringify(variable)} has not been read yet`,
          undefined,
          "park",
        );
      }
      return bound[variable];
    }
    default:
      throw new ExecutorError(
        `the attended executor cannot resolve a value source of kind ${record.kind}`,
        undefined,
        "park",
      );
  }
}

/** Refuse to navigate anywhere outside the recipe's allowed origins. */
export function assertAllowedOrigin(url: string, allowed: string[] | undefined): void {
  const origin = originOf(url);
  if (!origin) throw new ExecutorError(`the navigate step has no usable URL: ${url}`);
  if (allowed && allowed.length > 0 && !allowed.includes(origin)) {
    throw new ExecutorError(
      `refusing to navigate to ${origin}: it is not in allowed_origins`,
      undefined,
      "park",
    );
  }
}

/**
 * The execution context for a step's frame path. A step that names a frame but
 * whose context the page has not reported is a refusal: running it in the top
 * frame would act on the wrong document.
 *
 * PL-018b: a frame that is still loading is waited for, briefly. A step can arrive before the
 * iframe is attached or before its document has reported a context, so the tree and the context
 * are re-read every 100 ms for up to `waitMs`. Only then is the step refused.
 */
export async function frameContextId(
  cdp: CdpTransport,
  step: ExecutorStep,
  frames: FrameContexts | undefined,
  waitMs: number = FRAME_WAIT_MS,
): Promise<number | undefined> {
  if (!step.frame || step.frame.length === 0) return undefined;
  if (!frames) {
    throw new ExecutorError(
      `step ${step.id} targets a frame, but no frame contexts are available`,
      step.id,
      "park",
    );
  }
  const started = Date.now();
  for (;;) {
    const tree = await readFrameTree(cdp);
    const node = tree ? selectFrame(tree, step.frame) : null;
    const contextId = node ? frames.contextIdFor(node.frameId) : undefined;
    if (node && contextId !== undefined) return contextId;
    if (Date.now() - started >= waitMs) {
      if (!node) {
        // Say what was looked for and what the page has, so a park can be read without a debugger.
        const children = (tree?.children ?? []).map((child) => child.url || "(no url)");
        throw new ExecutorError(
          `the step's frame path did not resolve (wanted ${JSON.stringify(step.frame)}; the page ${tree ? `${tree.url} has ${children.length} frame(s): ${children.join(", ")}` : "has no frame tree"})`,
          step.id,
          "park",
        );
      }
      throw new ExecutorError(
        `step ${step.id} targets frame ${node.frameId}, whose execution context the page has not reported`,
        step.id,
        "park",
      );
    }
    await new Promise<void>((done) => setTimeout(done, 100));
  }
}

/** How long a step waits for its frame to appear and report a context (PL-018b). */
export const FRAME_WAIT_MS = 3000;

/** The condition-evaluation context for a run's dependencies. */
export function assertionContext(deps: StepRunDeps): ConditionContext {
  return { resolverSource: deps.resolverSource ?? resolverExpression() };
}

/**
 * Resolve the listbox a custom combobox controls, so the option search can be
 * scoped to it.
 *
 * A control that names its listbox (`aria-controls`/`aria-owns`) is trusted to
 * that element; otherwise the one open `[role=listbox]` is used. Searching the
 * whole document for a `role=option` would let an option belonging to another
 * listbox be clicked ------ the wrong record's value ------ so a listbox that cannot be
 * resolved is a refusal, not a document-wide search.
 *
 * The failure carries `kind` so a caller can tell "this widget never opened a popup" (`no_listbox`,
 * which is benign for a fill) from "the control names a listbox that is not in the document"
 * (`missing_listbox`, which is not: the popup the control describes is gone).
 */
type ListboxLookup =
  | { ok: true; descriptor: string }
  | { ok: false; reason: string; kind: "no_listbox" | "missing_listbox" | "other" };

async function resolveListboxDescriptor(
  cdp: CdpTransport,
  token: string,
  contextId: number | undefined,
): Promise<ListboxLookup> {
  const expression = `(() => {
    const el = ${markerExpression(token)};
    if (!el) return { ok: false, reason: "the select target is no longer in the document" };
    const firstId = (value) => String(value || "").split(/\\s+/).filter(Boolean)[0] || "";
    const id = firstId(el.getAttribute("aria-controls")) || firstId(el.getAttribute("aria-owns"));
    if (id) {
      if (!document.getElementById(id)) {
        return { ok: false, reason: "the listbox the control names is not in the document" };
      }
      return { ok: true, descriptor: "listbox:" + id };
    }
    const open = Array.from(document.querySelectorAll('[role="listbox"]')).filter((box) => {
      if (box.hasAttribute("hidden")) return false;
      const style = window.getComputedStyle ? window.getComputedStyle(box) : null;
      return !style || (style.display !== "none" && style.visibility !== "hidden");
    });
    if (open.length === 0) return { ok: false, reason: "the combobox has no listbox to search" };
    if (open.length > 1) return { ok: false, reason: "more than one listbox is open" };
    const box = open[0];
    if (!box.id) {
      return { ok: false, reason: "the open listbox has no id to scope the option search to" };
    }
    return { ok: true, descriptor: "listbox:" + box.id };
  })()`;
  const result = (await evaluate(
    cdp,
    expression,
    contextId !== undefined ? { contextId } : {},
  )) as { ok?: boolean; descriptor?: string; reason?: string } | undefined;
  if (result?.ok === true && typeof result.descriptor === "string") {
    return { ok: true, descriptor: result.descriptor };
  }
  const reason = result?.reason ?? "the combobox's listbox could not be resolved";
  if (reason === "the combobox has no listbox to search") return { ok: false, reason, kind: "no_listbox" };
  if (reason === "the listbox the control names is not in the document") {
    return { ok: false, reason, kind: "missing_listbox" };
  }
  return { ok: false, reason, kind: "other" };
}

/** Climb the 5-rung ladder for one step and return the resolver's result. */
async function resolveStep(
  cdp: CdpTransport,
  step: ExecutorStep,
  deps: StepRunDeps,
  contextId: number | undefined,
  ladder: ResolveRung[] = (step.targets ?? []) as ResolveRung[],
  preset?: ResolvedTarget,
): Promise<{ result: ResolveResult; token: string }> {
  // A target may be handed in already resolved and hit-tested by the guard phase. Resolving it a
  // second time doubles the CDP round trips for every step, and those round trips are the dominant
  // cost of a command against the runner's five-second ack budget. Reusing the guard's element is
  // also the more honest thing to act on: it is exactly the element the guards approved.
  if (preset) {
    return {
      result: {
        ok: true,
        rung: preset.rung,
        by: preset.by,
        name: preset.name,
        marker: preset.token,
        x: 0,
        y: 0,
        tag: preset.tag,
        attempts: [],
      },
      token: preset.token,
    };
  }
  const token = nextToken();
  const query: ResolveQuery = {
    targets: ladder,
    within: step.within ?? null,
    token,
  };
  const expression = resolverCallExpression(deps.resolverSource ?? resolverExpression(), query);
  const result = (await evaluate(
    cdp,
    expression,
    contextId !== undefined ? { contextId } : {},
  )) as ResolveResult;
  return { result, token };
}

/**
 * How much of a step's own timeout the ladder may spend waiting for the page to be ready, as a
 * share of that timeout, held between a floor and a ceiling.
 *
 * A page's `load` event is not the moment its application exists. Odoo's `/odoo/crm` answers with
 * an HTML shell of a few dozen elements and boots its web client afterwards, so a step sent right
 * after a navigate climbed three rungs against 68 elements and no inputs at all, found nothing, and
 * parked the record (PL-027). Waiting is the honest answer: the element is not absent, the page is
 * not ready. The wait is bounded by the step's own timeout -- the runner waits on the wire for
 * exactly that long -- so it cannot outlive the runner's patience, and a target that is really gone
 * is still refused, only after the wait.
 */
export const READY_WAIT_SHARE = 0.6;
export const READY_WAIT_MIN_MS = 250;
export const READY_WAIT_MAX_MS = 6000;
export const READY_WAIT_POLL_MS = 250;

/** The timeout a step that names none of its own is assumed to have, for the ready wait. */
const READY_WAIT_DEFAULT_MS = 8000;

/** How long this step may spend waiting for the page to be ready for it. */
function readyWaitMs(step: ExecutorStep): number {
  const own =
    typeof step.timeout_ms === "number" && step.timeout_ms > 0
      ? step.timeout_ms
      : READY_WAIT_DEFAULT_MS;
  const share = Math.floor(own * READY_WAIT_SHARE);
  return Math.min(READY_WAIT_MAX_MS, Math.max(READY_WAIT_MIN_MS, share));
}

/**
 * Climb the ladder, and climb it again until it resolves to one element or the ready wait is over.
 *
 * The climb itself is `resolveStep`; this is only the patience around it. It is used for a step's
 * own target ladder and never for the ladder built to read an autocomplete's suggestions -- that one
 * polls its own listbox and would otherwise double the waiting.
 */
async function resolveWhenReady(
  cdp: CdpTransport,
  step: ExecutorStep,
  deps: StepRunDeps,
  contextId: number | undefined,
  ladder?: ResolveRung[],
  preset?: ResolvedTarget,
): Promise<{ result: ResolveResult; token: string }> {
  if (preset) return resolveStep(cdp, step, deps, contextId, ladder, preset);
  const deadline = Date.now() + readyWaitMs(step);
  for (;;) {
    const attempt = await resolveStep(cdp, step, deps, contextId, ladder);
    // PL-048: every refusal is waited on, an ambiguous or disabled match included. Odoo draws an
    // order line ~170 ms after "Add a product", and until it does a css rung matches the customer's
    // autocomplete and one other; stopping at that first answer parked the record a moment before
    // the row's own input existed. Waiting never acts on an ambiguous target, it only delays the
    // same refusal by at most the ready wait when the page really is ambiguous.
    if (attempt.result.ok || Date.now() >= deadline) {
      return attempt;
    }
    await delay(Math.min(READY_WAIT_POLL_MS, Math.max(1, deadline - Date.now())));
  }
}

/**
 * Resolve and hit-test one step's target, and attach the marker. Sends no input:
 * this is the `prepare` half of a step, so the runner can learn where a click
 * would land before it authorises the click.
 */
export async function resolveStepTarget(
  step: ExecutorStep,
  deps: StepRunDeps,
): Promise<ResolvedTarget | ResolveFailure> {
  try {
    const contextId = await frameContextId(deps.cdp, step, deps.frames);
    if ((step.targets ?? []).length === 0) {
      return { ok: false, error: `step ${step.id} has no target ladder` };
    }
    const { result, token } = await resolveWhenReady(deps.cdp, step, deps, contextId);
    if (!result.ok) {
      return { ok: false, error: `${result.error ?? "no rung matched"}${attemptTrail(result)}` };
    }
    // A click lands at a measured box center, so its target is hit-tested here.
    // A fill or a select acts on the element itself and needs no box; measuring
    // one would refuse an off-screen field that a focus can still reach.
    if (step.action === "click") {
      try {
        await measureTarget(deps.cdp, token, contextId);
      } catch (error) {
        return { ok: false, error: error instanceof Error ? error.message : String(error) };
      }
    }
    return {
      ok: true,
      token,
      rung: result.rung,
      by: result.by,
      tag: result.tag,
      name: result.name ?? "",
      role: result.role,
    };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

/**
 * The accessible name carried by a resolver result. `resolveStep` returns a `ResolveResult`, whose
 * `name` is set by the page-side resolver when a rung matched.
 */
function nameOf(result: ResolveResult): string {
  const name = (result as { name?: unknown }).name;
  return typeof name === "string" ? name : "";
}

/**
 * Which rung was tried, how many usable elements it found, and why it was refused.
 *
 * A park that says only "no rung resolved" leaves a person with a working page and a task that
 * cannot run it, and nothing to look at: the rung trail is the difference between "the element is
 * gone" (0 matches on every rung) and "the ladder is ambiguous here" (a rung that matched twice).
 */
function attemptTrail(result: ResolveResult): string {
  const attempts = (result as { attempts?: unknown }).attempts;
  if (!Array.isArray(attempts) || attempts.length === 0) return "";
  const trail = attempts
    .map((entry) => {
      const rung = (entry as { rung?: unknown }).rung ?? "?";
      const by = String((entry as { by?: unknown }).by ?? "?");
      const matches = (entry as { matches?: unknown }).matches ?? 0;
      const found = (entry as { found?: unknown }).found;
      const reason = (entry as { reason?: unknown }).reason;
      const seen = typeof found === "number" ? `${found} matched, ${matches} usable` : `${matches} usable`;
      return `rung ${rung} (${by}): ${seen}${reason ? ` - ${reason}` : ""}`;
    })
    .join("; ");
  return ` [${trail}]`;
}

/**
 * The timeout a step's own waiting runs under, in milliseconds.
 *
 * A command-mode step always carries one: the runner writes `timeout_ms` onto the wire for every
 * command it sends, and the wire deadline it will wait for is that value plus its ack slack. A
 * command-mode step without one is refused rather than given the extension's own default, because
 * a default longer than the runner's wait is exactly how a legitimate 8s navigation was written
 * off at 5s and paused the run (R4).
 */
export function stepTimeoutMs(step: ExecutorStep, deps: StepRunDeps): number {
  if (typeof step.timeout_ms === "number") return step.timeout_ms;
  if (deps.commandMode === true) {
    throw new ExecutorError(
      `the step ${step.id} reached the command session with no timeout_ms; the runner must name the deadline it waits for`,
      step.id,
      "park",
    );
  }
  return DEFAULT_NAVIGATE_TIMEOUT_MS;
}

/** How often the popup is re-read while it opens: a debounced widget inserts its options late. */
export const AUTOCOMPLETE_POLL_MS = 100;

/** One read of a listbox: how many suggestions match, which they are, and which the widget has active. */
interface SuggestionPoll {
  ok?: boolean;
  reason?: string;
  count?: number;
  options?: Array<{ index: number; text?: string }>;
  activeIndex?: number;
  /** How many options the widget marks `aria-selected="true"`, when it marks no active descendant. */
  selectedCount?: number;
}

/** Wait `ms` milliseconds: the poll's own pause between two reads. */
function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** The element id a `listbox:<id>` descriptor names. */
function listboxIdOf(descriptor: string): string {
  const prefix = "listbox:";
  return descriptor.startsWith(prefix) ? descriptor.slice(prefix.length) : descriptor;
}

/**
 * The page-side matcher for one listbox's suggestions, as a function source so the poll and the
 * fallback stamp share exactly one definition of "this option is the typed value".
 *
 * An option matches when its normalised, lowercased text CONTAINS the typed value, or when one of
 * its value-shaped tokens (`data-address`, `data-email`, `data-value`, `value`, or any attribute
 * whose value parses as `x@y`) EQUALS it — a widget may show a person's name while the value it
 * will accept is their address. A match is counted by its index among the listbox's `role=option`
 * children, and `activeIndex` is the option the widget itself marks active: the id its
 * `aria-activedescendant` names (read on the listbox, else on the field), or, when neither carries
 * one, the single option marked `aria-selected="true"`.
 */
function suggestionMatcherSource(): string {
  return `function plSuggestionMatches(box, typed, field) {
    const norm = (value) => String(value === null || value === undefined ? "" : value).replace(/\\s+/g, " ").trim();
    const needle = norm(typed).toLowerCase();
    const addressish = (value) => /^[^\\s@]+@[^\\s@]+$/.test(value);
    const tokensOf = (el) => {
      const tokens = [];
      for (const name of ["data-address", "data-email", "data-value", "value"]) {
        const value = norm(el.getAttribute(name));
        if (value !== "") tokens.push(value);
      }
      for (const attr of Array.from(el.attributes || [])) {
        const value = norm(attr.value);
        if (value !== "" && addressish(value)) tokens.push(value);
      }
      return tokens;
    };
    const options = Array.from(box.querySelectorAll('[role="option"]'));
    const matches = [];
    options.forEach((el, index) => {
      const text = norm(el.textContent);
      const tokenHit = tokensOf(el).some((token) => token.toLowerCase() === needle);
      if (needle !== "" && (text.toLowerCase().includes(needle) || tokenHit)) {
        matches.push({ index: index, text: text });
      }
    });
    const activeId =
      norm(box.getAttribute("aria-activedescendant")) ||
      (field ? norm(field.getAttribute("aria-activedescendant")) : "");
    let activeIndex = -1;
    let selectedCount = 0;
    if (activeId !== "") {
      options.forEach((el, index) => {
        if (el.id === activeId) activeIndex = index;
      });
    } else {
      const selected = [];
      options.forEach((el, index) => {
        if (norm(el.getAttribute("aria-selected")).toLowerCase() === "true") selected.push(index);
      });
      selectedCount = selected.length;
      if (selected.length === 1) activeIndex = selected[0];
    }
    return {
      ok: true,
      count: matches.length,
      options: matches,
      activeIndex: activeIndex,
      selectedCount: selectedCount,
    };
  }`;
}

/** Read one listbox's suggestions out of the page. Re-resolves nothing else. */
function suggestionPollExpression(descriptor: string, typed: string, fieldToken: string): string {
  return `(() => {
    const plSuggestionMatches = ${suggestionMatcherSource()};
    const box = document.getElementById(${JSON.stringify(listboxIdOf(descriptor))});
    if (!box) return { ok: false, reason: "the listbox is no longer in the document" };
    const field = ${markerExpression(fieldToken)};
    return plSuggestionMatches(box, ${JSON.stringify(typed)}, field);
  })()`;
}

/**
 * Stamp the single matching option with `token`, after re-proving it is still the only match and
 * still a target a click could reach. The accessibility predicates are the ladder's own (`hidden`,
 * `display`/`visibility`, `disabled`/`aria-disabled`), plus the two a click needs and the ladder
 * does not ask for: a box with area to measure, and a `pointer-events` that lets the press land
 * there. An option that fails any of them is not a click target, and this fallback must not say it
 * is.
 */
function suggestionStampExpression(descriptor: string, typed: string, token: string): string {
  return `(() => {
    const plSuggestionMatches = ${suggestionMatcherSource()};
    const box = document.getElementById(${JSON.stringify(listboxIdOf(descriptor))});
    if (!box) return { ok: false, reason: "the listbox is no longer in the document" };
    const found = plSuggestionMatches(box, ${JSON.stringify(typed)}, null);
    if (found.count !== 1) {
      return { ok: false, reason: "the suggestion is no longer the only match (" + found.count + ")" };
    }
    const options = Array.from(box.querySelectorAll('[role="option"]'));
    const el = options[found.options[0].index];
    if (!el) return { ok: false, reason: "the suggestion is no longer in the listbox" };
    const view = document.defaultView;
    const style = view && view.getComputedStyle ? view.getComputedStyle(el) : null;
    if (el.hasAttribute("hidden")) return { ok: false, reason: "the suggestion is hidden" };
    if (style && (style.display === "none" || style.visibility === "hidden")) {
      return { ok: false, reason: "the suggestion is not rendered" };
    }
    if (style && style.pointerEvents === "none") {
      return { ok: false, reason: "the page refuses pointer events on the suggestion" };
    }
    if (el.disabled === true || String(el.getAttribute("aria-disabled") || "").toLowerCase() === "true") {
      return { ok: false, reason: "the suggestion is disabled" };
    }
    const rect = typeof el.getBoundingClientRect === "function" ? el.getBoundingClientRect() : null;
    if (!rect || rect.width <= 0 || rect.height <= 0) {
      return { ok: false, reason: "the suggestion has no box with area to click" };
    }
    const marker = ${JSON.stringify(TARGET_MARKER)};
    for (const marked of Array.from(document.querySelectorAll("[" + marker + "]"))) {
      marked.removeAttribute(marker);
    }
    el.setAttribute(marker, ${JSON.stringify(token)});
    return { ok: true };
  })()`;
}

/**
 * Complete a fill the recorder witnessed as an autocomplete (PL-018 §4.3).
 *
 * The recorder only sets `autocomplete: true` on a field whose suggestion the person accepted, so
 * on replay the fill is not finished until that suggestion is accepted too: a half-typed value
 * must never be submitted in its place. The suggestion is found by containment — one match is
 * accepted, zero or several park — and it is accepted by CLICKING the option through the guarded
 * click path, which is the deterministic accept the executor already proved safe.
 *
 * `Enter` is used only when the widget offers no clickable option AND its own active descendant
 * (or, when the widget marks none, its single selected option) is that one match: the widget is
 * then the one saying "press Enter accepts this", and the press is never blind. A field that
 * shows no listbox at all — a widget that never opened a popup — is left exactly as filled.
 */
async function completeAutocomplete(
  deps: StepRunDeps,
  step: ExecutorStep,
  contextId: number | undefined,
  token: string,
  typed: string,
  timeoutMs: number,
  hooks: InputHooks,
): Promise<void> {
  const scope = contextId !== undefined ? { contextId } : {};
  const listbox = await resolveListboxDescriptor(deps.cdp, token, contextId);
  if (!listbox.ok) {
    if (listbox.kind === "no_listbox") return;
    throw new ExecutorError(
      `the autocomplete step ${step.id} has a listbox it cannot use for the value ${JSON.stringify(
        typed,
      )}: ${listbox.reason}`,
      step.id,
      "park",
    );
  }

  // The popup opens (and filters) asynchronously after the last keystroke, so read it again on a
  // fixed interval until it offers a suggestion or the step's own deadline passes.
  const deadline = Date.now() + timeoutMs;
  let found: SuggestionPoll = { ok: false, reason: "the listbox was never read" };
  for (;;) {
    const read = (await evaluate(
      deps.cdp,
      suggestionPollExpression(listbox.descriptor, typed, token),
      scope,
    )) as SuggestionPoll | undefined;
    if (read?.ok === true) {
      found = read;
      if ((read.count ?? 0) > 0) break;
    }
    if (Date.now() >= deadline) break;
    await delay(Math.max(0, Math.min(AUTOCOMPLETE_POLL_MS, deadline - Date.now())));
  }

  const matches = found.options ?? [];
  if (matches.length === 0) {
    throw new ExecutorError(
      `no suggestion contains ${JSON.stringify(
        typed,
      )} in the listbox of step ${step.id}; the fill is refused rather than submitted half-written`,
      step.id,
      "park",
    );
  }
  if (matches.length > 1) {
    throw new ExecutorError(
      `more than one suggestion contains ${JSON.stringify(typed)} for step ${step.id} (${
        matches.length
      } matches); choosing one would be a guess`,
      step.id,
      "park",
    );
  }
  const only = matches[0] as { index: number; text?: string };
  const activeIndex = typeof found.activeIndex === "number" ? found.activeIndex : -1;

  // Whether the widget offers a *clickable* option is decided in the page, not by the ladder: a
  // keyboard-only popup can render an option the ladder would happily name while no pointer can
  // reach it. The probe re-proves the match is still the only one and stamps it, so the fallback
  // click has an element to act on.
  const stampedToken = nextToken();
  const stamped = (await evaluate(
    deps.cdp,
    suggestionStampExpression(listbox.descriptor, typed, stampedToken),
    scope,
  )) as { ok?: boolean; reason?: string } | undefined;
  if (stamped?.ok === true) {
    // The ladder's role rung is the accept route the guarded click path already trusts; the stamp
    // above is the fallback for a widget whose accessible name is not the option's own text (a
    // `title`, an `aria-label`, a name-plus-address split).
    const named = await resolveStep(deps.cdp, step, deps, contextId, [
      { by: "role", role: "option", name: only.text ?? "", within: listbox.descriptor },
    ]);
    await dispatchClick(
      deps.cdp,
      named.result.ok ? named.token : stampedToken,
      contextId,
      hooks,
      timeoutMs,
    );
    return;
  }

  // No clickable option. Enter is allowed only when the widget itself points at this match.
  if (activeIndex !== only.index) {
    const activeNote =
      (found.selectedCount ?? 0) > 1
        ? `the widget marks ${found.selectedCount} suggestions selected`
        : activeIndex === -1
          ? "the widget marks no suggestion active"
          : `the widget marks suggestion ${activeIndex} active, not this one`;
    throw new ExecutorError(
      `the only suggestion for ${JSON.stringify(typed)} in step ${step.id} is not clickable (${
        stamped?.reason ?? "the page refused it"
      }) and ${activeNote}; refusing to press Enter blind`,
      step.id,
      "park",
    );
  }
  await dispatchPress(deps.cdp, token, "Enter", contextId, hooks);
}

/**
 * Perform one step's action: navigate, fill, select, click, or press. It does NOT
 * verify `assert_after` ------ the caller does that, because in command mode a
 * separate `check` command carries the post-condition.
 *
 * `onInputDispatched` fires once, immediately after the trusted input event ------ for
 * a click after the mouse release, for a fill or a select after the last
 * keystroke or change. It is the extension's acknowledgement that the
 * destination has been handed the input (R2), and it is never fired for a step
 * that refused before sending anything.
 */
export async function performStep(
  step: ExecutorStep,
  context: RunContext,
  allowedOrigins: string[] | undefined,
  deps: StepRunDeps,
  onInputDispatched?: () => void,
  preset?: ResolvedTarget,
): Promise<StepReport> {
  const contextId = await frameContextId(deps.cdp, step, deps.frames);
  // `onInputDispatched` is the caller's acknowledgement that input reached the page. It fires at
  // the FIRST input event, not after the whole action (F2): an error after the first keystroke or
  // press means the destination may already have the change, which is a `lost` outcome the runner
  // reconciles, never a clean `not_dispatched`.
  const hooks: InputHooks = {
    ...(deps.beforeInput ? { beforeInput: deps.beforeInput } : {}),
    ...(onInputDispatched ? { onFirstInput: onInputDispatched } : {}),
  };

  if (step.action === "navigate") {
    const url = step.url ?? "";
    assertAllowedOrigin(url, allowedOrigins);
    await dispatchNavigate(deps.cdp, url, stepTimeoutMs(step, deps), hooks);
    // The arrival is what releases the abandon guard: the dirty document a previous record left
    // is gone, so it can no longer send anything, and from here a request the page makes belongs
    // to the page that is now on screen. The requests the guard refused are reported so the
    // runner can put them in the ledger (PL-027).
    const blocked = await disarmAbandonGuard(deps.cdp);
    return {
      id: step.id,
      action: step.action,
      outcome: "done",
      ...(blocked.length > 0 ? { observed: { blocked } } : {}),
    };
  }

  if (step.action === "press") {
    // A press acts on the focused element itself, not on a box: no `measureTarget`, exactly like a
    // fill or a select. The ladder still proves the element is the one the step named.
    const { result, token } = await resolveWhenReady(deps.cdp, step, deps, contextId, undefined, preset);
    if (!result.ok) {
      throw new ExecutorError(
        `the press target for step ${step.id} could not be resolved: ${result.error ?? "no rung matched"}${attemptTrail(result)}`,
        step.id,
        "park",
      );
    }
    await dispatchPress(deps.cdp, token, step.key ?? "", contextId, hooks);
    return { id: step.id, action: step.action, outcome: "done", rung: result.rung, by: result.by };
  }

  if (step.action === "fill") {
    const value = resolveValue(step.value, context);
    const text = value === undefined ? "" : String(value);
    if (text === "") {
      throw new ExecutorError(
        `the fill step ${step.id} resolved to an empty value; an empty fill is refused rather than submitted`,
        step.id,
        "park",
      );
    }
    const { result, token } = await resolveWhenReady(deps.cdp, step, deps, contextId, undefined, preset);
    if (!result.ok) {
      throw new ExecutorError(
        `the fill target for step ${step.id} could not be resolved: ${result.error ?? "no rung matched"}${attemptTrail(result)}`,
        step.id,
        "park",
      );
    }
    await dispatchFill(deps.cdp, token, text, {
      clearFirst: step.clear_first,
      contextId,
      hooks,
    });
    // A fill the recorder witnessed as an autocomplete is not finished until its suggestion is
    // accepted; an ordinary fill is byte-identical to before — no listbox read, no poll, no click.
    if (step.autocomplete === true) {
      await completeAutocomplete(deps, step, contextId, token, text, stepTimeoutMs(step, deps), hooks);
    }
    return { id: step.id, action: step.action, outcome: "done", rung: result.rung, by: result.by };
  }

  if (step.action === "select") {
    const value = resolveValue(step.value, context);
    const wanted = value === undefined ? "" : String(value);
    if (wanted === "") {
      // Fail-closed: a select with no value would clear or leave the field, silently submitting a
      // blank choice. A missing column must park the record, not guess (F3).
      throw new ExecutorError(
        `the select step ${step.id} has no value to choose; a missing input column parks the record rather than submitting a blank choice`,
        step.id,
        "park",
      );
    }
    const { result, token } = await resolveWhenReady(deps.cdp, step, deps, contextId, undefined, preset);
    if (!result.ok) {
      throw new ExecutorError(
        `the select target for step ${step.id} could not be resolved: ${result.error ?? "no rung matched"}${attemptTrail(result)}`,
        step.id,
        "park",
      );
    }
    const chosen = await dispatchSelect(
      deps.cdp,
      token,
      wanted,
      step.option_match ?? "text",
      contextId,
      hooks,
    );
    if (!chosen.matched && !chosen.custom) {
      throw new ExecutorError(
        `the select step ${step.id} could not choose ${JSON.stringify(wanted)}: ${chosen.reason ?? "no option matched"}`,
        step.id,
        "park",
      );
    }
    if (!chosen.matched && chosen.custom) {
      // A custom combobox: open it, then click the option inside the listbox it
      // owns. The search is `role=option` only, scoped to that listbox: a
      // document-wide search, or a fuzzy text fallback, could click an option of
      // some other listbox on the page.
      await dispatchClick(deps.cdp, token, contextId, hooks);
      const listbox = await resolveListboxDescriptor(deps.cdp, token, contextId);
      if (!listbox.ok) {
        throw new ExecutorError(
          `the select step ${step.id} could not find the listbox for ${wanted}: ${listbox.reason}`,
          step.id,
          "park",
        );
      }
      const option = await resolveStep(deps.cdp, step, deps, contextId, [
        { by: "role", role: "option", name: wanted, within: listbox.descriptor },
      ]);
      if (!option.result.ok) {
        throw new ExecutorError(
          `the select step ${step.id} found no option matching ${wanted} inside its listbox`,
          step.id,
          "park",
        );
      }
      await dispatchClick(deps.cdp, option.token, contextId, hooks);
    }
    return { id: step.id, action: step.action, outcome: "done", rung: result.rung, by: result.by };
  }

  // click
  //
  // A click is measured and hit-tested before it is pressed, and a real page can re-render the
  // element out from under the marker in between: Odoo's many2one autocomplete replaces its whole
  // option list when the search it typed comes back, so the option the ladder stamped was gone by
  // the time the box was measured (PL-027). Nothing has been pressed when that happens -- the
  // measure is before the press -- so climbing the ladder again is safe, and it is what a ladder is
  // for. The step's own deadline bounds the retries, and a click that landed is never repeated: only
  // the pre-press "the marked element is no longer in the document" is ever retried.
  const clickDeadline = Date.now() + stepTimeoutMs(step, deps);
  for (let attempt = 0; ; attempt += 1) {
    const { result, token } = await resolveWhenReady(
      deps.cdp,
      step,
      deps,
      contextId,
      undefined,
      attempt === 0 ? preset : undefined,
    );
    if (!result.ok) {
      throw new ExecutorError(
        `the click target for step ${step.id} could not be resolved: ${result.error ?? "no rung matched"}${attemptTrail(result)}`,
        step.id,
        "park",
      );
    }
    // The destructive-control invariant is checked HERE, at the last moment before the click, using
    // the accessible name of the element the ladder actually matched. That covers both the command
    // mode (whose handler checks it earlier, on the same name) and the local test run, and it cannot
    // be bypassed by a recipe that points at a bare CSS selector.
    if (!step.allow_destructive_control) {
      const blocked = isOption(result.role) ? null : destructiveMatch(nameOf(result));
      if (blocked) {
        throw new ExecutorError(
          `refusing to click a destructive control ("${blocked}"): set allow_destructive_control on step ${step.id} to permit it`,
          step.id,
          "park",
        );
      }
    }
    // The step's own deadline bounds how long the target may take to stop moving (PL-017b).
    try {
      await dispatchClick(deps.cdp, token, contextId, hooks, Math.max(1, clickDeadline - Date.now()));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (!message.includes("no longer in the document") || Date.now() >= clickDeadline) throw error;
      continue; // the page re-rendered under the marker; nothing was pressed, so climb again
    }
    return { id: step.id, action: step.action, outcome: "done", rung: result.rung, by: result.by };
  }
}
