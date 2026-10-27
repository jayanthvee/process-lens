// The recording session: everything one demonstration is, in one place.
//
// The content scripts see one page and then another; only the worker lives through a
// navigation. So the worker owns what the recording *is*: the order of its events, the step
// ids (one counter per action for the whole session, so a page load cannot restart them), the
// action a navigation is still owed evidence for, and how a top-level navigation nobody's
// click caused becomes part of the recording.
//
// Kept as a class with no browser APIs, so every one of those rules is a unit test.
import type { PendingEvent } from "../shared/events";
import type { Condition, RecordedEvent } from "../shared/recipe-types";
import type { RecordingState } from "../shared/messages";
import { isEmailHost } from "../shared/email-code";

/**
 * How long the page an action navigated to has to report before the action is no longer the
 * target. Long enough for a slow full-page save, short enough that a later, unrelated page load
 * cannot be mistaken for the one that was coming.
 */
export const ARRIVAL_TARGET_WINDOW_MS = 10_000;

/**
 * A top-level navigation, as `chrome.webNavigation` describes it. Only the fields the
 * recording decision reads are named.
 */
export interface NavigationDetails {
  frameId: number;
  transitionType: string;
  transitionQualifiers?: string[];
}

/** What the worker knows about the recording when a navigation commits. */
export interface NavigationContext {
  recording: boolean;
  /** Whether the navigation happened in the tab the recording is following. */
  recordedTab: boolean;
}

/**
 * Transition types that mean the person moved the page themselves: the address bar, the reload
 * button, a bookmark, or a start page. They are facts about the workflow ("go back to the
 * list"), so the recording keeps them.
 */
const PERSON_MOVED_TRANSITIONS = new Set([
  "typed",
  "reload",
  "auto_bookmark",
  "start_page",
  "keyword",
  "keyword_generated",
]);

/** The transition types a click or a form submit produces. Those are already recorded. */
const ACTION_TRANSITIONS = new Set(["link", "form_submit"]);

/**
 * Whether this navigation belongs in the recording (requirement 6: navigation is recorded).
 *
 * A navigation a recorded click or submit caused is already in the recording as that click, and
 * the transition data is what says so: a click leaves `link` or `form_submit` behind, and
 * anything else that is not the person moving the page themselves is a redirect or a script —
 * not a step. What remains is what the person did in the browser chrome: a typed URL, a
 * bookmark, a reload, a back/forward.
 */
export function shouldRecordNavigation(
  details: NavigationDetails,
  context: NavigationContext,
): boolean {
  if (!context.recording || !context.recordedTab) return false;
  if (details.frameId !== 0) return false; // a subframe is not the page the person is on
  // Back and forward first: Chrome reports them as a link (or a typed) transition plus the
  // `forward_back` qualifier, and the person did ask for those by pressing the button.
  if ((details.transitionQualifiers ?? []).includes("forward_back")) return true;
  if (ACTION_TRANSITIONS.has(details.transitionType)) return false;
  return PERSON_MOVED_TRANSITIONS.has(details.transitionType);
}

/** The actions that can commit, and so are the ones an observation can be attached to. */
const OBSERVABLE_KINDS = new Set(["click", "submit"]);

/**
 * Whether a step may be deleted from the live list (PL-043): only once the recording has stopped.
 *
 * While it is running the list is still being built, so a removal would race the page recording
 * into it. The worker checks this before every removal and the popup hides each step's ×; the
 * decision lives here so both read the same rule.
 */
export function canRemoveStep(state: RecordingState): boolean {
  // PL-113 item 7: a mistake is fixed while it is still being made, not only after Stop. Both
  // states offer removal; a removed step's own evidence goes with it.
  return state === "idle" || state === "recording";
}

export class RecorderSession {
  private readonly now: () => number;
  private readonly maxEvents: number;
  private stored: RecordedEvent[] = [];
  private sequence = 0;
  private readonly counters = new Map<string, number>();
  /**
   * The page's provisional step id → the session's stored id. The content script mints ids from
   * its own counter (`fill-2`) while the session re-stamps every step per action (`fill-1`), so an
   * envelope reference like PL-018's `supersedes` must be translated or it points at nothing.
   */
  private readonly idMap = new Map<string, string>();
  private arrival: { stepId: string; at: number; delivered: boolean } | null = null;

  constructor(deps: { now?: () => number; maxEvents?: number } = {}) {
    this.now = deps.now ?? (() => Date.now());
    this.maxEvents = deps.maxEvents ?? 5000;
  }

  /** Every recorded event, in order. */
  get events(): readonly RecordedEvent[] {
    return this.stored;
  }

  get count(): number {
    return this.stored.length;
  }

  /**
   * Adopt events that outlived this worker — storage keeps them across a restart. The step
   * counters are read back out of the ids, so a restarted worker keeps counting up instead of
   * re-minting ids that already exist.
   */
  restore(events: readonly RecordedEvent[]): void {
    this.stored = [...events];
    this.sequence = events.reduce((highest, event) => Math.max(highest, event.seq), 0);
    this.counters.clear();
    for (const event of events) this.countStepId(event.step?.id ?? "");
  }

  /**
   * Store one recorded event.
   *
   * The worker stamps the order and the step id: the id counter belongs to the *session*, not
   * to a page (requirement 5), so an id minted on one page keeps its place after a navigation
   * when the content script's own counter has started again. A step therefore arrives with a
   * provisional id and is stored under the session's own.
   *
   * An event on the person's own webmail (PL-107) is dropped and returns ``null``: going to the
   * inbox for a code is not a step of the task being taught.
   */
  append(pending: PendingEvent): RecordedEvent | null {
    if (isEmailHost(pending.url)) return null;
    this.sequence += 1;
    const provisional = typeof pending.step?.id === "string" ? pending.step.id : null;
    const step = pending.step
      ? { ...pending.step, id: this.nextStepId(pending.step.action) }
      : null;
    if (provisional && step) this.idMap.set(provisional, step.id);
    // An envelope reference to a step (PL-018's `supersedes`) is minted with the page's own
    // provisional id, and the step is re-stamped above. Translate it, or the reference would point
    // at an id no stored event carries and the compiler could not fold the pair.
    const supersedes =
      typeof pending.supersedes === "string"
        ? (this.idMap.get(pending.supersedes) ?? pending.supersedes)
        : undefined;
    const event: RecordedEvent = {
      ...pending,
      seq: this.sequence,
      step,
      ...(supersedes !== undefined ? { supersedes } : {}),
    };
    this.stored.push(event);
    if (this.stored.length > this.maxEvents) {
      this.stored.splice(0, this.stored.length - this.maxEvents);
    }
    // PL-110: a fill followed by a click on an option is a pick, not a plain fill — the box
    // searched as the person typed and they took a suggestion. Marking the fill lets the replay
    // wait for the list and choose the option instead of refusing a box that rewrote itself.
    if (this.stepIsOption(event.step)) this.markNewestFillAsPick();
    return event;
  }

  /** Whether a step's own ladder names a suggestion option. */
  private stepIsOption(step: RecordedEvent["step"]): boolean {
    for (const rung of step?.targets ?? []) {
      if (rung.by === "role" && (rung as { role?: string }).role?.toLowerCase() === "option") {
        return true;
      }
    }
    return false;
  }

  /** Mark the newest fill as a pick, once. It is the fill the option belongs to. */
  private markNewestFillAsPick(): void {
    for (let index = this.stored.length - 1; index >= 0; index -= 1) {
      const event = this.stored[index]!;
      if (event.step?.action !== "fill") continue;
      if (event.autocomplete === true) return;
      this.stored[index] = { ...event, autocomplete: true };
      return;
    }
  }

  /** Record a navigation nobody's click caused. It has no step: it moved the page, nothing acted. */
  appendNavigate(url: string): RecordedEvent | null {
    return this.append({
      at: new Date(this.now()).toISOString(),
      kind: "navigate",
      url,
      step: null,
    });
  }

  clear(): void {
    this.stored = [];
    this.sequence = 0;
    this.counters.clear();
    this.idMap.clear();
    this.arrival = null;
  }

  /**
   * Delete one recorded step, and the evidence recorded right after it (PL-043).
   *
   * The live list offers this only once the recording has stopped (`canRemoveStep`), so this is a
   * finished recording's mistake being fixed. An `observation` event carries no step of its own:
   * it belongs to the action recorded just before it — the link the arrival path keeps across a
   * navigation for the other kind of observation — so it goes with that action. Evidence attached
   * to the action itself (`event.observation`) goes with the event. Nothing else is touched, and
   * the sequence keeps counting from where it was: ids are never re-minted for a new step.
   *
   * Returns the removed step, or null when no event carries that seq.
   */
  remove(seq: number): RecordedEvent | null {
    const index = this.stored.findIndex((event) => event.seq === seq);
    if (index < 0) return null;
    const [removed] = this.stored.splice(index, 1);
    if (!removed) return null;
    // The observation recorded straight after the action is that action's evidence.
    if (removed.kind !== "observation" && this.stored[index]?.kind === "observation") {
      this.stored.splice(index, 1);
    }
    // A removed step cannot be the target of the page a navigation is still owed to.
    const stepId = removed.step?.id;
    if (stepId !== undefined && this.arrival?.stepId === stepId) this.arrival = null;
    return removed;
  }

  /** Forget what a navigation was owed. A new recording owes nothing to the last one. */
  forgetArrival(): void {
    this.arrival = null;
  }

  /** The arrival target as stored, so a worker restart does not drop the evidence it is owed. */
  snapshotArrival(): { stepId: string; at: number } | null {
    return this.arrival ? { stepId: this.arrival.stepId, at: this.arrival.at } : null;
  }

  restoreArrival(value: { stepId: string; at: number } | null): void {
    this.arrival = value ? { ...value, delivered: false } : null;
  }

  /**
   * Keep the newest unobserved action as the target of the navigation that is starting.
   *
   * Called when a document unloads right after an action it recorded and had not observed yet.
   * The action is the one that caused this navigation, so whatever the destination shows is its
   * evidence. Returns whether there was such an action.
   */
  noteLeaving(): boolean {
    const newest = this.newestAction();
    const stepId = newest?.step?.id;
    if (!newest || !stepId) return false;
    this.arrival = { stepId, at: this.now(), delivered: false };
    return true;
  }

  /**
   * The step a freshly loaded page should watch for, or null when the session is not owed an
   * observation. A target older than the window is dropped: the page it belonged to is not
   * coming, and a later load must not inherit it.
   */
  pendingTarget(): string | null {
    const arrival = this.arrival;
    if (!arrival) return null;
    if (this.now() - arrival.at > ARRIVAL_TARGET_WINDOW_MS) {
      this.arrival = null;
      return null;
    }
    return arrival.stepId;
  }

  /**
   * Hand the target to the page that asked, once. Only one document is the page the action
   * navigated to; every other asker — a later load, another frame — gets nothing, so the
   * evidence can only ever be attached by the page that actually saw the confirmation.
   */
  takePendingTarget(): string | null {
    const target = this.pendingTarget();
    if (target === null || this.arrival?.delivered) return null;
    this.arrival = { ...this.arrival!, delivered: true };
    return target;
  }

  /**
   * Attach what the arriving page showed to the action that navigated to it.
   *
   * The report is only taken for the step the session is waiting on, and only while that step
   * carries no evidence yet: the first observation wins, and a report that arrives late, or for
   * a step that is not the pending one, changes nothing.
   */
  attachObservation(stepId: string, observation: Condition): RecordedEvent | null {
    if (this.pendingTarget() !== stepId) return null;
    const index = this.stored.findIndex((event) => event.step?.id === stepId);
    if (index < 0) return null;
    const existing = this.stored[index]!;
    if (existing.observation) return null;
    const updated: RecordedEvent = { ...existing, observation };
    this.stored[index] = updated;
    this.arrival = null;
    return updated;
  }

  /** The newest recorded click or submit that has no observation yet. */
  private newestAction(): RecordedEvent | null {
    for (let index = this.stored.length - 1; index >= 0; index -= 1) {
      const event = this.stored[index]!;
      if (!OBSERVABLE_KINDS.has(event.kind)) continue;
      if (event.observation) return null;
      return event;
    }
    return null;
  }

  private nextStepId(action: string): string {
    const next = (this.counters.get(action) ?? 0) + 1;
    this.counters.set(action, next);
    return `${action}-${next}`;
  }

  /** Read a stored step id back into the counter it came from, when it has the session's shape. */
  private countStepId(id: string): void {
    const match = /^(.*)-(\d+)$/.exec(id);
    if (!match) return;
    const action = match[1]!;
    const ordinal = Number(match[2]);
    if (!Number.isFinite(ordinal)) return;
    this.counters.set(action, Math.max(this.counters.get(action) ?? 0, ordinal));
  }
}
