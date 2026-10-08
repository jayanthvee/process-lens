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
  isPasswordField,
  normalizeText,
  semanticRole,
} from "../shared/fingerprint";
import { makeEvent, nextStepId, type PendingEvent } from "../shared/events";
import type { ClickStep, FillStep, SelectStep, RecordedStep } from "../shared/recipe-types";

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

/** Climb from the event target to the element the person meant to act on. */
function resolveClickTarget(start: Element): Element {
  let el: Element | null = start;
  while (el && el.tagName.toUpperCase() !== "BODY") {
    if (el.matches?.(CLICKABLE_SELECTOR)) return el;
    el = el.parentElement;
  }
  return start;
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
  const url = hooks.url ?? (() => doc.location?.href ?? "");
  const stamp = hooks.now ?? (() => new Date().toISOString());
  const frame = computeFramePath(doc.defaultView ?? window);

  const active = (): boolean => hooks.isActive();

  function emitStep(kind: PendingEvent["kind"], step: RecordedStep): void {
    hooks.send(makeEvent({ kind, url: url(), step, frame, at: stamp() }));
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

  /** Emit a click or a submit, then watch the page for what it changed. */
  function emitAction(kind: "click" | "submit", step: RecordedStep | null): void {
    hooks.send(makeEvent({ kind, url: url(), step, frame, at: stamp() }));
    beginObservation();
  }

  function emitObservation(payload: Record<string, unknown>, at: string): void {
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
    emitStep("fill", fillStep(el, value));
  }

  function onClick(event: Event): void {
    if (!active()) return;
    const start = elementFrom(event.target);
    if (!start) return;
    const el = resolveClickTarget(start);
    if (isPasswordField(el) || isPasswordField(start)) return;

    const role = semanticRole(el);
    if (TYPING_ROLES.has(role)) return; // typing is captured as a fill
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
    if (isPasswordField(el)) {
      pending.delete(el);
      return;
    }
    const tag = el.tagName.toUpperCase();
    if (tag === "INPUT") {
      const type = inputType(el);
      if (type === "checkbox" || type === "radio" || type === "file") return;
    }
    pending.set(el, valueOf(el));
  }

  function onChange(event: Event): void {
    if (!active()) return;
    const el = elementFrom(event.target);
    if (!el) return;
    if (isPasswordField(el)) {
      pending.delete(el);
      return;
    }
    const tag = el.tagName.toUpperCase();

    if (tag === "SELECT") {
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
      const value = valueOf(el) || normalizeText(el.textContent);
      if (value) emitStep("select", selectStep(el, value));
      pending.delete(el);
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

  doc.addEventListener("click", onClick, true);
  doc.addEventListener("input", onInput, true);
  doc.addEventListener("change", onChange, true);
  doc.addEventListener("focusout", onFocusOut, true);
  doc.addEventListener("submit", onSubmit, true);

  return () => {
    doc.removeEventListener("click", onClick, true);
    doc.removeEventListener("input", onInput, true);
    doc.removeEventListener("change", onChange, true);
    doc.removeEventListener("focusout", onFocusOut, true);
    doc.removeEventListener("submit", onSubmit, true);
    endObservation();
    pending.clear();
  };
}

/**
 * Wire the recorder to the extension runtime. Kept out of `attachRecorder` so the
 * recorder can be tested with a plain document and no browser APIs.
 */
function bootstrap(): void {
  const ext = (globalThis as { chrome?: typeof chrome }).chrome;
  if (!ext?.runtime?.id) return;

  let recording = false;
  const send = (event: PendingEvent): void => {
    if (!recording) return;
    void ext.runtime.sendMessage({ type: "record:event", event }).catch(() => undefined);
  };

  attachRecorder(document, { send, isActive: () => recording });

  ext.runtime.onMessage.addListener((message: unknown) => {
    const incoming = message as { type?: string; status?: { state?: string } };
    if (incoming?.type === "record:state") {
      recording = incoming.status?.state === "recording";
    }
  });

  void ext.runtime
    .sendMessage({ type: "record:get" })
    .then((response: unknown) => {
      const current = (response as { status?: { state?: string } } | undefined)?.status;
      if (current) recording = current.state === "recording";
    })
    .catch(() => undefined);
}

bootstrap();
