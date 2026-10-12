// The semantic recorder: turns what a person does on a page into ordered,
// replayable steps.
//
// It listens for clicks, typing, select changes, and form submits, and describes
// each target by meaning (role, name, container, test id, label, and a stable
// selector) rather than by coordinates. A password field is never captured: its
// value is not read, buffered, or sent.
import {
  computeFramePath,
  computeTargets,
  describeElement,
  hasOnlyPositionalTargets,
  isCredentialContext,
  isPasswordField,
  isRichText,
  normalizeText,
  semanticRole,
} from "../shared/fingerprint";
import { makeEvent, nextStepId, type PendingEvent } from "../shared/events";
import type { ClickStep, Condition, FillStep, SelectStep, RecordedStep } from "../shared/recipe-types";

/** Roles whose value is typed, so a click on them is focus noise, not an action. */
const TYPING_ROLES = new Set(["textbox", "searchbox", "spinbutton"]);
const TOGGLE_ROLES = new Set(["checkbox", "radio", "switch"]);

/**
 * The event kind that carries what the page showed after an action. It is not a
 * step: it is evidence about the click or submit just before it, and the compiler
 * folds it onto that step as the assertion for a commit.
 */
const OBSERVATION_KIND = "observation";

/** How long to watch the page after an action, in milliseconds. */
const DEFAULT_OBSERVATION_WINDOW_MS = 1500;

/**
 * The recorder's caveat for a step whose only remaining rung is a positional path.
 * It rides the event envelope, not the step, so a compiled recipe stays schema-valid.
 */
export const POSITIONAL_ONLY_WARNING = "only a positional selector remains";

/**
 * How long the page an action navigated to looks for a confirmation after it loads.
 *
 * A full-page save cannot be observed from the page that sent it — that document is gone
 * before the destination renders anything. So the page that arrives reads the text instead,
 * for the three seconds the ticket allows.
 */
export const ARRIVAL_OBSERVATION_WINDOW_MS = 3000;

/**
 * Elements that read as a confirmation: an alert, a status message, a live
 * region, a toast, or a notification. One of these appearing after an action is
 * the strongest evidence that the destination accepted the change.
 */
const CONFIRMATION_SELECTOR = [
  "[role='alert']",
  "[role='status']",
  "[aria-live='polite']",
  "[aria-live='assertive']",
  ".toast",
  ".notification",
].join(",");

const HEADING_SELECTOR = "h1, h2, h3, h4, h5, h6";
const OBSERVATION_TEXT_LIMIT = 200;

const CLICKABLE_SELECTOR = [
  "button",
  "a[href]",
  "input",
  "select",
  "textarea",
  "summary",
  "[role='button']",
  "[role='link']",
  "[role='tab']",
  "[role='menuitem']",
  "[role='option']",
  "[role='checkbox']",
  "[role='radio']",
  "[role='switch']",
  "[contenteditable='true']",
].join(",");

/**
 * The clickable elements that *act* when clicked, as opposed to the ones that merely hold a
 * value. This is the set the recorder checks for a name of its own before it settles for an
 * enclosing chip, option or form.
 */
const BUTTON_LIKE_SELECTOR = [
  "button",
  "a[href]",
  "summary",
  "input[type='submit']",
  "input[type='button']",
  "input[type='reset']",
  "input[type='image']",
  "[role='button']",
  "[role='link']",
  "[role='tab']",
  "[role='menuitem']",
].join(",");

export interface RecorderHooks {
  /** Send one captured event onward. Called only while `isActive()` is true. */
  send: (event: PendingEvent) => void;
  /** Whether recording is on right now. */
  isActive: () => boolean;
  /** The page URL to stamp on events. Defaults to the document's own URL. */
  url?: () => string;
  /** Injectable clock, for deterministic tests. */
  now?: () => string;
  /** How long to watch for a confirmation after an action. Defaults to 1500ms. */
  observationWindowMs?: number;
  /**
   * Called when this document is unloading right after an action it recorded and has not
   * observed yet. The action is about to navigate; whatever the destination shows is the
   * evidence for it, and only the service worker can carry that across the navigation.
   */
  onLeaving?: () => void;
  /**
   * The step a page this one replaced recorded just before it navigated here. When present,
   * the arrived page reports the first confirmation it shows as that step's evidence.
   */
  arrival?: { stepId: string };
  /** Receives an arrival observation, once, when the loaded page shows a confirmation. */
  onArrivalObservation?: (stepId: string, observation: Condition) => void;
}

function elementFrom(target: EventTarget | null): Element | null {
  if (!target || (target as Element).nodeType !== 1) return null;
  return target as Element;
}

function inputType(el: Element): string {
  return (el.getAttribute("type") ?? "text").toLowerCase();
}

function valueOf(el: Element): string {
  const value = (el as HTMLInputElement | HTMLTextAreaElement).value;
  return typeof value === "string" ? value : "";
}

/** What the person typed: a control's value, or the text of a rich-text body. */
function typedValueOf(el: Element): string {
  if (isRichText(el)) return normalizeText(el.textContent);
  return valueOf(el);
}

function selectedOptionText(el: Element): string {
  const select = el as HTMLSelectElement;
  const option = select.selectedOptions?.[0] ?? select.options?.[select.selectedIndex ?? -1];
  return normalizeText(option?.textContent ?? select.value);
}

function trimmedText(el: Element | null, limit = OBSERVATION_TEXT_LIMIT): string {
  const text = normalizeText(el?.textContent ?? "");
  return text.length <= limit ? text : text.slice(0, limit);
}

/**
 * The path pattern a navigation landed on, with the leaf record id dropped:
 * `/records/42` becomes `/records/`. A single-segment path is kept whole.
 */
function navigationPattern(href: string): string | null {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  const path = url.pathname;
  if (!path) return null;
  if (path.endsWith("/")) return path;
  const slash = path.lastIndexOf("/");
  return slash > 0 ? path.slice(0, slash + 1) : path;
}

/** Whether two URLs share a scheme and host. */
function sameOrigin(left: string, right: string): boolean {
  try {
    return new URL(left).origin === new URL(right).origin;
  } catch {
    return false;
  }
}

/** The name a control gives itself: its own label, not the text it happens to contain. */
function ownNameOf(el: Element): string {
  const ariaLabel = normalizeText(el.getAttribute("aria-label"));
  if (ariaLabel) return ariaLabel;
  const labelledby = normalizeText(el.getAttribute("aria-labelledby"));
  if (labelledby) return labelledby;
  return normalizeText(el.getAttribute("title"));
}

/** A `<label>` has no action of its own: the control it labels is what a click means. */
function adoptedControl(el: Element): Element {
  if (el.tagName.toUpperCase() !== "LABEL") return el;
  const control = (el as HTMLLabelElement).control;
  return control ?? el;
}

interface ResolvedClick {
  /** The element the click is recorded against. */
  element: Element;
  /** True when the click landed on a `<label>` and was read as its control. */
  viaLabel: boolean;
}

/**
 * Climb from the event target to the element the person meant to act on.
 *
 * A *named* button-like element wins over everything around it: the ✕ on a recipient chip
 * carries its own `aria-label="Remove …"`, and clicking it removes the recipient — it is not
 * the chip, the option or the form it sits in, however clickable those look. Failing that,
 * the innermost clickable ancestor is the target, and a click on a `<label>` is recorded
 * against the control that label names.
 */
function resolveClickTarget(start: Element): ResolvedClick {
  for (let el: Element | null = start; el && el.tagName.toUpperCase() !== "BODY"; el = el.parentElement) {
    if (el.matches?.(BUTTON_LIKE_SELECTOR) && ownNameOf(el)) return { element: el, viaLabel: false };
  }

  for (let el: Element | null = start; el && el.tagName.toUpperCase() !== "BODY"; el = el.parentElement) {
    if (el.matches?.(CLICKABLE_SELECTOR)) {
      const control = adoptedControl(el);
      return { element: control, viaLabel: control !== el };
    }
  }

  const label = start.closest?.("label") ?? null;
  if (label) {
    const control = adoptedControl(label);
    if (control !== label) return { element: control, viaLabel: true };
  }
  return { element: start, viaLabel: false };
}

function firstSubmitControl(form: Element): Element | null {
  return form.querySelector(
    "button[type='submit'], input[type='submit'], button:not([type])",
  );
}

/**
 * Attach the recorder to a document. Returns a function that detaches it.
 *
 * The hooks make this testable without a browser: tests pass a document, a
 * collector for `send`, and a flag for `isActive`.
 */
export function attachRecorder(doc: Document, hooks: RecorderHooks): () => void {
  const pending = new Map<Element, string>();
  /**
   * The value each element's typing was last recorded with. A combobox reports the same entry
   * twice — once as `input`/`focusout` and once as `change` — and this is what says so.
   */
  const recordedTyping = new Map<Element, string>();
  const url = hooks.url ?? (() => doc.location?.href ?? "");
  const stamp = hooks.now ?? (() => new Date().toISOString());
  const frame = computeFramePath(doc.defaultView ?? window);

  const active = (): boolean => hooks.isActive();

  /**
   * The recorder's caveat for a step, or undefined when at least one rung is durable.
   * This is the one place the reason is decided; every event that carries a step goes
   * through `stepEvent`, so no path can forget it.
   */
  function stepWarning(step: RecordedStep | null): string | undefined {
    return step && hasOnlyPositionalTargets(step.targets) ? POSITIONAL_ONLY_WARNING : undefined;
  }

  /** The event that carries a step, with its warning attached when it has one. */
  function stepEvent(kind: PendingEvent["kind"], step: RecordedStep | null): PendingEvent {
    const warning = stepWarning(step);
    return makeEvent({
      kind,
      url: url(),
      step,
      frame,
      at: stamp(),
      ...(warning ? { warning } : {}),
    });
  }

  function emitStep(kind: PendingEvent["kind"], step: RecordedStep): void {
    hooks.send(stepEvent(kind, step));
  }

  const observationWindowMs = hooks.observationWindowMs ?? DEFAULT_OBSERVATION_WINDOW_MS;

  interface ObservationWindow {
    startUrl: string;
    observedUrl: string | null;
    preexisting: Set<Element>;
    headings: string[];
  }

  let watching: ObservationWindow | null = null;
  let observer: MutationObserver | null = null;
  let debounce: ReturnType<typeof setTimeout> | null = null;
  let deadline: ReturnType<typeof setTimeout> | null = null;
  let stopUrlWatch: (() => void) | null = null;

  /**
   * Whether the last action this document recorded still has no evidence. While it is true the
   * document, if it unloads, is unloading because of that action, and says so on `pagehide`.
   */
  let actionAwaitingEvidence = false;

  /** Emit a click or a submit, then watch the page for what it changed. */
  function emitAction(kind: "click" | "submit", step: RecordedStep | null): void {
    hooks.send(stepEvent(kind, step));
    actionAwaitingEvidence = true;
    beginObservation();
  }

  function emitObservation(payload: Record<string, unknown>, at: string): void {
    actionAwaitingEvidence = false;
    hooks.send(
      makeEvent({
        kind: OBSERVATION_KIND,
        url: at,
        step: null,
        observation: payload,
        at: stamp(),
      }),
    );
  }

  function confirmationText(observation: ObservationWindow): string {
    for (const node of Array.from(doc.querySelectorAll(CONFIRMATION_SELECTOR))) {
      if (observation.preexisting.has(node)) continue;
      const text = trimmedText(node);
      if (text) return text;
    }
    // A heading that was not there before is weaker evidence, but still evidence.
    const headings = Array.from(doc.querySelectorAll(HEADING_SELECTOR)).map((el) =>
      normalizeText(el.textContent),
    );
    return headings.find((text) => text && !observation.headings.includes(text)) ?? "";
  }

  /** The first confirmation text on this page right now, if any. */
  function presentConfirmationText(): string {
    for (const node of Array.from(doc.querySelectorAll(CONFIRMATION_SELECTOR))) {
      const text = trimmedText(node);
      if (text) return text;
    }
    return "";
  }

  function armDebounce(): void {
    if (debounce !== null) clearTimeout(debounce);
    debounce = setTimeout(finishObservation, observationWindowMs);
  }

  function endObservation(): void {
    watching = null;
    observer?.disconnect();
    observer = null;
    stopUrlWatch?.();
    stopUrlWatch = null;
    if (debounce !== null) {
      clearTimeout(debounce);
      debounce = null;
    }
    if (deadline !== null) {
      clearTimeout(deadline);
      deadline = null;
    }
  }

  function finishObservation(): void {
    const observation = watching;
    if (!observation) return;

    const confirmation = confirmationText(observation);
    const navigated =
      observation.observedUrl && observation.observedUrl !== observation.startUrl
        ? observation.observedUrl
        : null;
    endObservation();
    if (!active()) return;

    if (confirmation) {
      emitObservation({ text_visible: confirmation }, url());
      return;
    }
    if (navigated) {
      const pattern = navigationPattern(navigated);
      if (pattern && sameOrigin(observation.startUrl, navigated)) {
        emitObservation({ url_matches: pattern }, navigated);
      }
    }
  }

  /**
   * Watch the page after an action for the two things that prove it landed: a
   * confirmation appearing, or the URL moving. Either is reported on its own
   * `observation` event; the compiler turns it into the step's assertion.
   */
  function beginObservation(): void {
    endObservation();
    if (!active()) return;

    const observation: ObservationWindow = {
      startUrl: url(),
      observedUrl: null,
      preexisting: new Set(Array.from(doc.querySelectorAll(CONFIRMATION_SELECTOR))),
      headings: Array.from(doc.querySelectorAll(HEADING_SELECTOR)).map((el) =>
        normalizeText(el.textContent),
      ),
    };
    watching = observation;

    const ObserverCtor = doc.defaultView?.MutationObserver;
    if (ObserverCtor) {
      observer = new ObserverCtor(() => armDebounce());
      observer.observe(doc.documentElement ?? doc, {
        childList: true,
        subtree: true,
        characterData: true,
      });
    }

    stopUrlWatch = watchUrl(() => {
      if (!watching) return;
      watching.observedUrl = url();
      armDebounce();
    });

    armDebounce();
    // A page that mutates forever must still report; cap the window.
    deadline = setTimeout(finishObservation, observationWindowMs * 2);
  }

  /**
   * Report what the page an action navigated to shows.
   *
   * There is no action in this document to watch *after*, so this is not an observation window:
   * the confirmation is already there when the page loads (a server-rendered flash message) or
   * appears moments later, and either one is the evidence the worker is holding the target for.
   * The page's own text is read; a field's value never is.
   */
  function beginArrivalWindow(stepId: string): void {
    let reported = false;
    let arrivalObserver: MutationObserver | null = null;
    let arrivalDeadline: ReturnType<typeof setTimeout> | null = null;

    const stop = (): void => {
      arrivalObserver?.disconnect();
      arrivalObserver = null;
      if (arrivalDeadline !== null) {
        clearTimeout(arrivalDeadline);
        arrivalDeadline = null;
      }
    };

    const report = (): void => {
      if (reported || !active()) return;
      const text = presentConfirmationText();
      if (!text) return;
      reported = true;
      stop();
      hooks.onArrivalObservation?.(stepId, { text_visible: text });
    };

    const ObserverCtor = doc.defaultView?.MutationObserver;
    if (ObserverCtor) {
      arrivalObserver = new ObserverCtor(() => report());
      arrivalObserver.observe(doc.documentElement ?? doc, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: ["hidden", "class", "style"],
      });
    }
    // The window the ticket allows: three seconds from load (this script runs at document_idle).
    arrivalDeadline = setTimeout(stop, ARRIVAL_OBSERVATION_WINDOW_MS);
    report();
  }

  /**
   * Report URL changes that fire no event: `pushState` and `replaceState` are
   * silent, so the history methods are wrapped for the duration of the window.
   */
  function watchUrl(notify: () => void): () => void {
    const view = doc.defaultView;
    const history = view?.history;
    const originalPush = history?.pushState?.bind(history) ?? null;
    const originalReplace = history?.replaceState?.bind(history) ?? null;

    if (history && originalPush) {
      history.pushState = ((...args: Parameters<History["pushState"]>) => {
        originalPush(...args);
        notify();
      }) as History["pushState"];
    }
    if (history && originalReplace) {
      history.replaceState = ((...args: Parameters<History["replaceState"]>) => {
        originalReplace(...args);
        notify();
      }) as History["replaceState"];
    }

    const onHash = (): void => notify();
    view?.addEventListener?.("hashchange", onHash);
    view?.addEventListener?.("popstate", onHash);

    return () => {
      if (history && originalPush) history.pushState = originalPush;
      if (history && originalReplace) history.replaceState = originalReplace;
      view?.removeEventListener?.("hashchange", onHash);
      view?.removeEventListener?.("popstate", onHash);
    };
  }

  function clickStep(el: Element): ClickStep {
    return {
      id: nextStepId("click"),
      action: "click",
      name: `Click ${describeElement(el)}`,
      targets: computeTargets(el),
    };
  }

  function fillStep(el: Element, value: string): FillStep {
    return {
      id: nextStepId("fill"),
      action: "fill",
      name: `Fill ${describeElement(el)}`,
      targets: computeTargets(el),
      value: { kind: "constant", value },
      clear_first: true,
    };
  }

  function selectStep(el: Element, value: string): SelectStep {
    return {
      id: nextStepId("select"),
      action: "select",
      name: `Select ${describeElement(el)}`,
      targets: computeTargets(el),
      value: { kind: "constant", value },
      option_match: "text",
    };
  }

  function flushFill(el: Element): void {
    const value = pending.get(el);
    pending.delete(el);
    // Defence in depth: a password field is never read, buffered, or sent.
    if (value === undefined || value === "" || isPasswordField(el)) return;
    // Remember what this element's value was recorded as, so the `change` that follows the very
    // same typing is not recorded a second time as the same action (see the combobox branch).
    recordedTyping.set(el, value);
    emitStep("fill", fillStep(el, value));
  }

  function onClick(event: Event): void {
    if (!active()) return;
    const start = elementFrom(event.target);
    if (!start) return;
    const { element: el, viaLabel } = resolveClickTarget(start);
    if (isPasswordField(el) || isPasswordField(start)) return;

    const role = semanticRole(el);
    // A click on a label is the person naming the control they want, so it is recorded against
    // that control even where a click directly on it would be focus noise. A toggle or a select
    // still records itself through its change event, so those are left to it rather than
    // recording the same action twice.
    if (!viaLabel && TYPING_ROLES.has(role)) return; // typing is captured as a fill
    if (TOGGLE_ROLES.has(role)) return; // the change event captures the toggle
    if (el.tagName.toUpperCase() === "SELECT") return; // the change event captures it

    if (role === "option") {
      const combo = el.closest("[role='combobox'], select");
      const text = normalizeText(el.textContent);
      if (combo && text) {
        emitStep("select", selectStep(combo, text));
        return;
      }
    }
    emitAction("click", clickStep(el));
  }

  function onInput(event: Event): void {
    if (!active()) return;
    const el = elementFrom(event.target);
    if (!el) return;
    if (isPasswordField(el) || (isRichText(el) && isCredentialContext(el))) {
      pending.delete(el);
      return;
    }
    const tag = el.tagName.toUpperCase();
    // A select reports its change through `change`; buffering its value here would let a later
    // focusout flush it as a second, invented fill.
    if (tag === "SELECT") return;
    if (tag === "INPUT") {
      const type = inputType(el);
      if (type === "checkbox" || type === "radio" || type === "file") return;
    }
    // Typing resumed: whatever was recorded for this element before is no longer what it holds.
    recordedTyping.delete(el);
    pending.set(el, typedValueOf(el));
  }

  function onChange(event: Event): void {
    if (!active()) return;
    const el = elementFrom(event.target);
    if (!el) return;
    if (isPasswordField(el) || (isRichText(el) && isCredentialContext(el))) {
      pending.delete(el);
      return;
    }
    const tag = el.tagName.toUpperCase();

    if (tag === "SELECT") {
      // One change, one event: drop any buffered value so no focusout can flush a second one.
      pending.delete(el);
      emitStep("select", selectStep(el, selectedOptionText(el)));
      return;
    }
    if (tag === "INPUT") {
      const type = inputType(el);
      if (type === "checkbox" || type === "radio") {
        emitAction("click", clickStep(el));
        return;
      }
      if (type === "file") return;
    }
    if (semanticRole(el) === "combobox") {
      // One entry into a combobox is one action, whichever order the browser reports it in. A
      // field the person typed into is a fill of what it now holds; a value that appeared with
      // no typing (an option was chosen) is a select. And a `change` that merely repeats a value
      // already recorded for this element is the same action, not a second one.
      const value = valueOf(el) || normalizeText(el.textContent);
      const typed = pending.has(el);
      pending.delete(el);
      if (recordedTyping.get(el) === value) {
        recordedTyping.delete(el);
        return;
      }
      if (!value) return;
      if (typed) {
        recordedTyping.set(el, value);
        emitStep("fill", fillStep(el, value));
        return;
      }
      emitStep("select", selectStep(el, value));
      return;
    }
    flushFill(el);
  }

  function onFocusOut(event: Event): void {
    if (!active()) return;
    const el = elementFrom(event.target);
    if (el && pending.has(el)) flushFill(el);
  }

  function onSubmit(event: Event): void {
    if (!active()) return;
    const form = elementFrom(event.target);
    if (!form || form.tagName.toUpperCase() !== "FORM") return;

    for (const el of Array.from(pending.keys())) {
      if (form.contains(el)) flushFill(el);
    }

    const submitter = (event as SubmitEvent).submitter ?? null;
    const control = submitter ?? firstSubmitControl(form);
    if (isPasswordField(control ?? form)) {
      emitAction("submit", null);
      return;
    }
    const step = control ? clickStep(control) : null;
    emitAction("submit", step ?? clickStep(form));
  }

  /**
   * The document is going away. If its last action has no evidence yet, the action is what
   * sent it away, and the worker keeps that step as the target for the page that lands.
   */
  function onPageHide(): void {
    if (!active() || !actionAwaitingEvidence) return;
    hooks.onLeaving?.();
  }

  doc.addEventListener("click", onClick, true);
  doc.addEventListener("input", onInput, true);
  doc.addEventListener("change", onChange, true);
  doc.addEventListener("focusout", onFocusOut, true);
  doc.addEventListener("submit", onSubmit, true);
  doc.defaultView?.addEventListener?.("pagehide", onPageHide);

  if (hooks.arrival?.stepId) beginArrivalWindow(hooks.arrival.stepId);

  return () => {
    doc.removeEventListener("click", onClick, true);
    doc.removeEventListener("input", onInput, true);
    doc.removeEventListener("change", onChange, true);
    doc.removeEventListener("focusout", onFocusOut, true);
    doc.removeEventListener("submit", onSubmit, true);
    doc.defaultView?.removeEventListener?.("pagehide", onPageHide);
    endObservation();
    pending.clear();
    recordedTyping.clear();
  };
}

/**
 * Wire the recorder to the extension runtime. Kept out of `attachRecorder` so the
 * recorder can be tested with a plain document and no browser APIs.
 *
 * One question goes to the service worker before the recorder attaches: is there an action the
 * page this one replaced recorded, whose confirmation only this page can read? The answer
 * carries the recording state too, so one round trip serves both.
 */
function bootstrap(): void {
  const ext = (globalThis as { chrome?: typeof chrome }).chrome;
  if (!ext?.runtime?.id) return;

  let recording = false;
  const send = (event: PendingEvent): void => {
    if (!recording) return;
    void ext.runtime.sendMessage({ type: "record:event", event }).catch(() => undefined);
  };

  const attach = (arrivalStepId: string | null): void => {
    attachRecorder(document, {
      send,
      isActive: () => recording,
      onLeaving: () => {
        if (!recording) return;
        void ext.runtime.sendMessage({ type: "record:leaving" }).catch(() => undefined);
      },
      onArrivalObservation: (stepId, observation) => {
        void ext.runtime
          .sendMessage({ type: "record:observation", stepId, observation })
          .catch(() => undefined);
      },
      ...(arrivalStepId ? { arrival: { stepId: arrivalStepId } } : {}),
    });
  };

  ext.runtime.onMessage.addListener((message: unknown) => {
    const incoming = message as { type?: string; status?: { state?: string } };
    if (incoming?.type === "record:state") {
      recording = incoming.status?.state === "recording";
    }
  });

  void ext.runtime
    .sendMessage({ type: "record:pending-observation" })
    .then((response: unknown) => {
      const current = response as
        | { status?: { state?: string }; observationTarget?: string | null }
        | undefined;
      if (current?.status) recording = current.status.state === "recording";
      attach(current?.observationTarget ?? null);
    })
    .catch(() => attach(null));
}

bootstrap();
