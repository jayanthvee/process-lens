// The semantic recorder: turns what a person does on a page into ordered,
// replayable steps.
//
// It listens for clicks, typing, select changes, and form submits, and describes
// each target by meaning (role, name, container, test id, label, and a stable
// selector) rather than by coordinates. A password field is never captured: its
// value is not read, buffered, or sent.
import {
  accessibleName,
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
import { makeEvent, nextStepId, resetStepIds, type PendingEvent } from "../shared/events";
import {
  chipAncestorOf,
  chipListAncestor,
  chipValueOf,
  collectSnapshot,
  diffSnapshot,
  isVisibleElement,
  type Snapshot,
} from "./snapshot";
import type {
  ClickStep,
  Condition,
  FillStep,
  PressKey,
  PressStep,
  RecordedStep,
  SelectStep,
} from "../shared/recipe-types";

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
 * The envelope warning a checkpoint emits when a field's value changed with nothing typing into it.
 * The value is still recorded — that is the whole reason the checkpoint exists — but the
 * provenance rides the envelope so a page-set value is visible rather than silent.
 */
export const PAGE_SET_VALUE_WARNING = "this field changed without any typing: the page set it";

/**
 * The warning an `unrecordable` event carries when a chip left a list with no remove button
 * clicked: the recording knows the recipient is gone and cannot replay how.
 */
export const UNRECORDABLE_CHIP_WARNING =
  "a recipient was removed without a remove button; the recording cannot replay it";

/**
 * One pointer gesture is one checkpoint: a `click` this soon after its own `pointerdown` does not
 * take a second one. Many autocompletes accept a suggestion on the press, so the press is where the
 * typed prefix has to be seen — and undoing it at the click would see the chip instead.
 */
const GESTURE_CHECKPOINT_WINDOW_MS = 700;

/** A control whose accessible name begins with this verb is the chip's remove button. */
const REMOVE_NAME = /^remove\b/i;

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

/**
 * Whether a control is marked required: the native `required` attribute, or the
 * ARIA spelling a custom control uses. It is a note about the event, not the step.
 */
function isRequiredField(el: Element): boolean {
  if ((el as HTMLInputElement).required === true) return true;
  return el.getAttribute("aria-required") === "true";
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
 * What `attachRecorder` returns: the detacher, with the checkpoint the callers that need one can
 * ask for explicitly (a navigation, a stop). A caller that only detaches keeps working unchanged.
 */
export type RecorderHandle = (() => void) & { checkpoint: () => void };

/**
 * Attach the recorder to a document. Returns a function that detaches it.
 *
 * The hooks make this testable without a browser: tests pass a document, a
 * collector for `send`, and a flag for `isActive`.
 */
export function attachRecorder(doc: Document, hooks: RecorderHooks): RecorderHandle {
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

  // ---- The PL-018 checkpoint's state ---------------------------------------------------------
  /** The page as the previous checkpoint left it. The first checkpoint is the baseline. */
  let baseline: Snapshot | null = null;
  /** The value each element's fill was last recorded with, so a diff cannot repeat a step. */
  const lastEmitted = new Map<Element, string>();
  /** The fields the recorder watched offer suggestions for. */
  const witnessedAutocomplete = new Set<Element>();
  /** Chip values a remove click already accounts for: the click is the step, not the removal. */
  const removedByControl = new Set<string>();
  /** Elements the person typed into or put their hand on since the last checkpoint. */
  const touched = new Set<Element>();
  /** When the last pointerdown checkpoint ran, in ms; see GESTURE_CHECKPOINT_WINDOW_MS. */
  let lastGestureCheckpoint = 0;
  /** The element the last fill was emitted for: the fallback owner of an unowned chip list. */
  let lastFillElement: Element | null = null;
  /**
   * The last *typed* fill's id per field. A chip-add fill supersedes one of these and never another
   * chip-add fill: superseding one chip with the next would drop a real recipient from the fold.
   */
  const lastTypedFillId = new Map<Element, string>();

  /**
   * The recorder's caveat for a step, or undefined when at least one rung is durable.
   * This is the one place the reason is decided; every event that carries a step goes
   * through `stepEvent`, so no path can forget it.
   */
  function stepWarning(step: RecordedStep | null): string | undefined {
    return step && hasOnlyPositionalTargets(step.targets) ? POSITIONAL_ONLY_WARNING : undefined;
  }

  /** The event that carries a step, with its warning attached when it has one. */
  function stepEvent(
    kind: PendingEvent["kind"],
    step: RecordedStep | null,
    required = false,
  ): PendingEvent {
    const warning = stepWarning(step);
    return makeEvent({
      kind,
      url: url(),
      step,
      frame,
      at: stamp(),
      ...(warning ? { warning } : {}),
      ...(required ? { required: true } : {}),
    });
  }

  function emitStep(kind: PendingEvent["kind"], step: RecordedStep, required = false): void {
    hooks.send(stepEvent(kind, step, required));
  }

  /**
   * The event that carries a step, plus the envelope notes a checkpoint's own steps add. The
   * recorder's caveat about the step is kept alongside them: a page-set value that also left only a
   * positional selector behind is two true things at once, and a person needs to see both.
   */
  function annotatedStepEvent(
    kind: PendingEvent["kind"],
    step: RecordedStep | null,
    notes: {
      warning?: string;
      required?: boolean;
      chip?: boolean;
      autocomplete?: boolean;
      supersedes?: string;
      reason?: string;
    },
  ): PendingEvent {
    const warning =
      [notes.warning, stepWarning(step)]
        .filter((note): note is string => Boolean(note))
        .join("; ") || undefined;
    return makeEvent({
      kind,
      url: url(),
      step,
      frame,
      at: stamp(),
      ...(warning ? { warning } : {}),
      ...(notes.required ? { required: true } : {}),
      ...(notes.chip !== undefined ? { chip: notes.chip } : {}),
      ...(notes.autocomplete !== undefined ? { autocomplete: notes.autocomplete } : {}),
      ...(notes.supersedes !== undefined ? { supersedes: notes.supersedes } : {}),
      ...(notes.reason !== undefined ? { reason: notes.reason } : {}),
    });
  }

  /** Whether an element is something a person types text into. */
  function isTextEntryElement(el: Element): boolean {
    const tag = el.tagName.toUpperCase();
    if (tag === "INPUT" || tag === "TEXTAREA") return true;
    if (isRichText(el)) return true;
    return el.matches("[role='textbox']");
  }

  /**
   * The key a `keydown` records, or null for the ones that are not a press. A printable single
   * character is typing, never a press: the fill carries the value it produced.
   */
  function pressKeyOf(event: KeyboardEvent, el: Element | null): PressKey | null {
    if (event.altKey) return null;
    const raw = event.key;
    if (!raw || raw.length === 1) return null;
    if (raw === "Escape") return "Escape";
    if (raw === "Enter") {
      if (event.ctrlKey) return "Control+Enter";
      if (event.metaKey) return "Meta+Enter";
      return "Enter";
    }
    if (raw === "Tab") return "Tab";
    if (raw === "Backspace" || raw === "Delete") {
      // Inside a text field this is editing, and the checkpoint records the resulting value.
      return el && isTextEntryElement(el) ? null : raw;
    }
    return null;
  }

  /** The element that has focus, descending open shadow roots and same-origin frames. */
  function deepActiveElement(root: Document | ShadowRoot): Element | null {
    let el = root.activeElement;
    while (el) {
      const shadow = (el as Element & { shadowRoot?: ShadowRoot | null }).shadowRoot ?? null;
      if (shadow?.activeElement) {
        el = shadow.activeElement;
        continue;
      }
      if (el.tagName.toUpperCase() === "IFRAME") {
        let inner: Document | null = null;
        try {
          inner = (el as HTMLIFrameElement).contentDocument;
        } catch {
          inner = null;
        }
        if (inner?.activeElement) {
          el = inner.activeElement;
          continue;
        }
      }
      break;
    }
    return el;
  }

  /** The element whose `aria-controls`/`aria-owns` names the given list, if any. */
  function ownerOfList(list: Element): Element | null {
    const id = list.getAttribute("id");
    if (!id) return null;
    const tokens = (field: Element, name: string): string[] =>
      normalizeText(field.getAttribute(name))
        .split(" ")
        .filter((token) => token.length > 0);
    for (const field of Array.from(doc.querySelectorAll("[aria-controls], [aria-owns]"))) {
      if (tokens(field, "aria-controls").includes(id)) return field;
      if (tokens(field, "aria-owns").includes(id)) return field;
    }
    return null;
  }

  /** Whether two elements share an ancestor: a chip list and the field that owns it do. */
  function sharesAncestor(field: Element, list: Element): boolean {
    for (let node: Element | null = list; node; node = node.parentElement) {
      if (node.contains(field)) return true;
    }
    return false;
  }

  /**
   * The field a chip list belongs to: the field focused inside its container, else the field that
   * points at it with `aria-controls`/`aria-owns`, else the field the last fill was emitted for.
   */
  function controllingField(list: Element): Element | null {
    const focused = doc.activeElement;
    if (focused && isTextEntryElement(focused) && sharesAncestor(focused, list)) return focused;
    const owner = ownerOfList(list);
    if (owner && isTextEntryElement(owner)) return owner;
    return lastFillElement;
  }

  /** Fields the page showed suggestions for: a recorded option, or a live listbox, witnesses one. */
  function witnessAutocomplete(): void {
    for (const list of Array.from(doc.querySelectorAll("[role='listbox']"))) {
      if (!isVisibleElement(list)) continue;
      const field = ownerOfList(list);
      if (field) witnessedAutocomplete.add(field);
    }
  }

  /**
   * Take the checkpoint: what the page changed since the last one, emitted as steps before the
   * action that triggered this checkpoint is emitted. The first checkpoint is the baseline and
   * emits nothing, which is why a form the person found prefilled produces no steps.
   */
  function checkpoint(): void {
    if (!active()) return;

    // Typing still in the buffer is emitted first, so it precedes whatever this checkpoint is for.
    const flushed = new Set<Element>();
    for (const el of Array.from(pending.keys())) {
      flushed.add(el);
      flushFill(el);
    }

    const current = collectSnapshot(doc);
    if (baseline === null) {
      baseline = current;
      touched.clear();
      return;
    }

    const diff = diffSnapshot(baseline, current);

    for (const fill of diff.fills) {
      if (flushed.has(fill.element) || lastEmitted.get(fill.element) === fill.value) continue;
      const step = fillStep(fill.element, fill.value);
      lastEmitted.set(fill.element, fill.value);
      lastFillElement = fill.element;
      lastTypedFillId.set(fill.element, step.id);
      hooks.send(
        annotatedStepEvent("fill", step, {
          ...(touched.has(fill.element) ? {} : { warning: PAGE_SET_VALUE_WARNING }),
          ...(isRequiredField(fill.element) ? { required: true } : {}),
        }),
      );
    }

    for (const change of diff.selects) {
      if (flushed.has(change.element) || lastEmitted.get(change.element) === change.value) continue;
      lastEmitted.set(change.element, change.value);
      emitStep(
        "select",
        selectStep(change.element, change.value),
        isRequiredField(change.element),
      );
    }

    witnessAutocomplete();

    for (const added of diff.chipsAdded) {
      const field = controllingField(added.list);
      const supersedes = field ? lastTypedFillId.get(field) : undefined;
      const step: FillStep = {
        id: nextStepId("fill"),
        action: "fill",
        name: `Fill ${field ? describeElement(field) : "recipients"}`,
        // The ladder is computed lazily, here, for the one chip list the diff reported — never
        // during the snapshot, which reads every field on the page.
        targets: field ? computeTargets(field) : computeTargets(added.list),
        value: { kind: "constant", value: added.value },
        // The chip is already in the field: clearing would delete a recipient the person kept.
        clear_first: false,
      };
      if (field) lastFillElement = field;
      hooks.send(
        annotatedStepEvent("fill", step, {
          chip: true,
          autocomplete: field ? witnessedAutocomplete.has(field) : false,
          ...(supersedes ? { supersedes } : {}),
        }),
      );
    }

    for (const removed of diff.chipsRemoved) {
      // A remove control the person clicked is the step for the chip going away; a chip that
      // vanished with no click is not replayable, so it is reported rather than dropped.
      if (removedByControl.has(removed.value)) continue;
      hooks.send(
        annotatedStepEvent("unrecordable", null, {
          reason: "chip-removal",
          warning: UNRECORDABLE_CHIP_WARNING,
        }),
      );
    }

    removedByControl.clear();
    touched.clear();
    baseline = current;
  }

  /** A pointer gesture's own checkpoint: many autocompletes accept a suggestion on the press. */
  function onPointerDown(): void {
    if (!active()) return;
    checkpoint();
    lastGestureCheckpoint = Date.now();
  }

  function onKeyDown(event: KeyboardEvent): void {
    if (!active()) return;
    if (event.altKey) return;
    const el = deepActiveElement(doc) ?? elementFrom(event.target);
    if (!el) return;
    const key = pressKeyOf(event, el);
    if (!key) return;
    // A key sent to a password field is never recorded, exactly as a click or a fill is not.
    if (isPasswordField(el) || (isRichText(el) && isCredentialContext(el))) return;
    // The typing that preceded this key is emitted first.
    checkpoint();
    const step: PressStep = {
      id: nextStepId("press"),
      action: "press",
      name: `Press ${key}`,
      targets: computeTargets(el),
      key,
    };
    emitStep("press", step);
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

  /**
   * The confirmation the window found, and the node it came from. A heading that was not there
   * before is weaker evidence, but still evidence — and it has no busy state of its own.
   */
  function confirmationIn(observation: ObservationWindow): { text: string; node: Element | null } {
    for (const node of Array.from(doc.querySelectorAll(CONFIRMATION_SELECTOR))) {
      if (observation.preexisting.has(node)) continue;
      const text = trimmedText(node);
      if (text) return { text, node };
    }
    const headings = Array.from(doc.querySelectorAll(HEADING_SELECTOR)).map((el) =>
      normalizeText(el.textContent),
    );
    const text = headings.find((heading) => heading && !observation.headings.includes(heading)) ?? "";
    return { text, node: null };
  }

  /**
   * Whether a confirmation is itself a progress indicator: a spinner or a busy region says work is
   * still running, so the text it shows is weaker evidence than a settled message (§8.2).
   */
  function isBusyConfirmation(node: Element): boolean {
    if (node.getAttribute("aria-busy") === "true") return true;
    if (normalizeText(node.getAttribute("role")).toLowerCase() === "progressbar") return true;
    return node.querySelector("[aria-busy='true'], [role='progressbar']") !== null;
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

    const found = confirmationIn(observation);
    const confirmation = found.text;
    const navigated =
      observation.observedUrl && observation.observedUrl !== observation.startUrl
        ? observation.observedUrl
        : null;
    endObservation();
    if (!active()) return;

    if (confirmation) {
      emitObservation(
        {
          text_visible: confirmation,
          ...(found.node && isBusyConfirmation(found.node) ? { busy: true } : {}),
        },
        url(),
      );
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
    const step = fillStep(el, value);
    lastEmitted.set(el, value);
    lastFillElement = el;
    lastTypedFillId.set(el, step.id);
    emitStep("fill", step, isRequiredField(el));
  }

  function onClick(event: Event): void {
    if (!active()) return;
    // The checkpoint runs before the click's own step, so the typing that produced it is emitted
    // first. A pointer gesture already checkpointed at its pointerdown and does not run again here:
    // one gesture, one checkpoint, one set of steps.
    if (Date.now() - lastGestureCheckpoint > GESTURE_CHECKPOINT_WINDOW_MS) checkpoint();
    const start = elementFrom(event.target);
    if (!start) return;
    const { element: el, viaLabel } = resolveClickTarget(start);
    if (isPasswordField(el) || isPasswordField(start)) return;

    const role = semanticRole(el);
    // A click on a field is focus, not an action -- but it is the person's hand on the field, so a
    // value the page changes afterwards is not a page-set value.
    if (TYPING_ROLES.has(role)) touched.add(el);

    // A control whose name says "Remove", sitting inside a chip list, is the step for that chip
    // leaving: the checkpoint that later sees it gone must not call the removal unreplayable.
    const removeName = normalizeText(ownNameOf(el) || accessibleName(el));
    if (REMOVE_NAME.test(removeName)) {
      const chip = chipAncestorOf(el);
      const value = chip ? chipValueOf(chip) : null;
      if (chipListAncestor(el) && value) removedByControl.add(value);
    }

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
        // The field showed suggestions and the person took one: this field is an autocomplete.
        witnessedAutocomplete.add(combo);
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
    touched.add(el);
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
      const value = selectedOptionText(el);
      // Remembered, so the checkpoint that follows the change does not record it again.
      lastEmitted.set(el, value);
      touched.add(el);
      emitStep("select", selectStep(el, value), isRequiredField(el));
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
      touched.add(el);
      if (recordedTyping.get(el) === value) {
        recordedTyping.delete(el);
        return;
      }
      if (!value) return;
      if (typed) {
        recordedTyping.set(el, value);
        const step = fillStep(el, value);
        lastEmitted.set(el, value);
        lastFillElement = el;
        lastTypedFillId.set(el, step.id);
        emitStep("fill", step);
        return;
      }
      lastEmitted.set(el, value);
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

    // Whatever the page changed before it submitted is emitted before the submit itself.
    checkpoint();

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
    // The document is going away: whatever changed since the last checkpoint is emitted now, while
    // there is still a page to read it from.
    checkpoint();
    hooks.onLeaving?.();
  }

  doc.addEventListener("click", onClick, true);
  doc.addEventListener("input", onInput, true);
  doc.addEventListener("change", onChange, true);
  doc.addEventListener("focusout", onFocusOut, true);
  doc.addEventListener("submit", onSubmit, true);
  // The checkpoint's own listeners: a pointer press may be the action, and a key can be one too.
  doc.addEventListener("pointerdown", onPointerDown, true);
  doc.addEventListener("keydown", onKeyDown as EventListener, true);
  doc.defaultView?.addEventListener?.("pagehide", onPageHide);

  if (hooks.arrival?.stepId) beginArrivalWindow(hooks.arrival.stepId);

  const detach = ((): void => {
    doc.removeEventListener("click", onClick, true);
    doc.removeEventListener("input", onInput, true);
    doc.removeEventListener("change", onChange, true);
    doc.removeEventListener("focusout", onFocusOut, true);
    doc.removeEventListener("submit", onSubmit, true);
    doc.removeEventListener("pointerdown", onPointerDown, true);
    doc.removeEventListener("keydown", onKeyDown as EventListener, true);
    doc.defaultView?.removeEventListener?.("pagehide", onPageHide);
    endObservation();
    pending.clear();
    recordedTyping.clear();
    baseline = null;
    lastEmitted.clear();
    touched.clear();
    removedByControl.clear();
    lastTypedFillId.clear();
    lastFillElement = null;
  }) as RecorderHandle;
  // A caller that has just navigated, or is about to stop, takes the last checkpoint itself.
  detach.checkpoint = checkpoint;
  return detach;
}

/**
 * Whether a `record:state` status means this document's step ids must restart at one.
 *
 * A new recording starts at one (the previous demonstration's numbers mean nothing to it), and a
 * clear — the worker broadcasting `idle` with nothing left recorded — restarts them too. A stop
 * does not: the recording it belongs to is still there, and the service worker renumbers the
 * events it already holds. Resetting on attach would be wrong for the same reason: a page loaded
 * mid-recording continues that recording's count rather than restarting it.
 */
export function shouldResetStepIds(
  incoming: { state?: string; count?: number } | undefined,
  previous: { state?: string; count?: number } | undefined,
): boolean {
  const state = incoming?.state;
  if (state === "recording") return previous?.state !== "recording";
  // A clear leaves the state `idle` and takes the count back to zero; a stop leaves it above zero.
  if (state === "idle") return (incoming?.count ?? 0) === 0 && (previous?.count ?? 0) > 0;
  return false;
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
  let previousStatus: { state?: string; count?: number } | undefined;
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
    const incoming = message as { type?: string; status?: { state?: string; count?: number } };
    if (incoming?.type === "record:state") {
      const status = incoming.status;
      // A new recording, or a clear, restarts the ids; a stop and an ongoing recording do not.
      if (shouldResetStepIds(status, previousStatus)) resetStepIds();
      previousStatus = status;
      recording = status?.state === "recording";
    }
  });

  void ext.runtime
    .sendMessage({ type: "record:pending-observation" })
    .then((response: unknown) => {
      const current = response as
        | { status?: { state?: string; count?: number }; observationTarget?: string | null }
        | undefined;
      if (current?.status) {
        // The state this page loaded into is the previous one for every change that follows.
        previousStatus = current.status;
        recording = current.status.state === "recording";
      }
      attach(current?.observationTarget ?? null);
    })
    .catch(() => attach(null));
}

bootstrap();
