// The state-diff engine: what the page holds now, and what changed since the last look.
//
// This file listens to nothing. The recorder owns the event path; this is the one
// page-side pass the recorder takes at a checkpoint (before a click, a press, a
// submit, a navigation, and stop), and the diff between two of those passes. It
// exists because the DOM reports far less than a person does: a rich-text body
// mutated by script fires no `input`, and a suggestion accepted on pointerdown
// clears the field before the click ever lands.
//
// Two rules are load-bearing and are the reason this is one file rather than a
// few lines in the recorder:
//
//   1. A password field is never read. `isPasswordField` and `isCredentialContext`
//      are called *before* any value is touched, exactly as the recorder calls them.
//   2. A suggestion popup is never a chip list. An autocomplete's own
//      `[role='listbox']` is structurally identical to the container of accepted
//      recipients, and reading it as one would invent a recipient for every
//      suggestion the person merely saw.
import {
  isCredentialContext,
  isPasswordField,
  isRichText,
  normalizeText,
} from "../shared/fingerprint";

/**
 * One compound query for the whole candidate set, so the cost is the browser's own walk rather
 * than ours. Shadow roots and same-origin frames are descended into from these candidates.
 */
const CANDIDATE_SELECTOR = [
  "input",
  "textarea",
  "select",
  "[contenteditable='true']",
  "[contenteditable='plaintext-only']",
  "[role='textbox']",
  "[role='listbox']",
  "[role='list']",
  "ul",
  "ol",
].join(",");

/** A container that can hold chips: a listbox, a list role, or a plain list element. */
const CHIP_CONTAINER_SELECTOR = "[role='listbox'], [role='list'], ul, ol";

/** A chip inside one of those containers. */
const CHIP_NODE_SELECTOR = "[role='option'], [role='listitem'], li";

/** A control whose accessible name starts with this verb is the chip's remove button. */
const REMOVE_NAME = /^(remove|delete|unassign)\b/i;

/** A leading verb plus its separator, stripped before what remains is parsed as an address. */
const LEADING_VERB = /^[A-Za-z]+\s+/;

/** What an address-shaped value looks like: one `@`, no whitespace on either side. */
const ADDRESS = /^[^@\s]+@[^@\s]+$/;

/** How many candidates one snapshot reads before it stops. */
export const SNAPSHOT_LIMIT = 1500;

/** One element's state, as the snapshot read it. Targets and names are NOT read here.
 *
 * They are the expensive part (a document-wide `label[for]` query per field, a positional path
 * per rung), and a snapshot reads every field on the page while a diff reports a handful. The
 * element reference is carried instead, and the recorder computes the ladder only for the entries
 * the diff reports as changed.
 */
export interface SnapshotEntry {
  kind: "text" | "select" | "chip";
  value: string;
  chipValues: string[];
}

/** One pass over the page. `entries` is keyed by element identity, which a diff needs. */
export interface Snapshot {
  entries: Map<Element, SnapshotEntry>;
  document: Document;
  truncated: boolean;
}

/** What changed between two passes. An item that vanished contributes nothing. */
export interface SnapshotDiff {
  fills: { element: Element; value: string; entry: SnapshotEntry }[];
  selects: { element: Element; value: string; entry: SnapshotEntry }[];
  chipsAdded: { list: Element; value: string }[];
  chipsRemoved: { list: Element; value: string }[];
}

interface WalkState {
  truncated: boolean;
  crossOrigin: boolean;
}

function attributeTokens(el: Element, name: string): string[] {
  return normalizeText(el.getAttribute(name)).split(" ").filter((token) => token.length > 0);
}

/** Whether anything on the page points at this element with `aria-controls`/`aria-owns`. */
function isControlledBy(field: Element, target: Element): boolean {
  const id = target.getAttribute("id");
  if (!id) return false;
  return (
    attributeTokens(field, "aria-controls").includes(id) ||
    attributeTokens(field, "aria-owns").includes(id)
  );
}

/** The element whose `aria-controls`/`aria-owns` names the given list, if any. */
function controllerOf(list: Element): Element | null {
  const doc = list.ownerDocument;
  if (!doc) return null;
  for (const node of Array.from(doc.querySelectorAll("[aria-controls], [aria-owns]"))) {
    if (isControlledBy(node, list)) return node;
  }
  return null;
}

/**
 * Whether an element is visible. `checkVisibility` is the browser's own answer where it exists;
 * otherwise a zero-rect element is the DOM's way of saying the same thing.
 */
export function isVisibleElement(el: Element): boolean {
  const check = (el as Element & { checkVisibility?: () => boolean }).checkVisibility;
  if (typeof check === "function") return check.call(el) !== false;
  return el.getClientRects().length > 0;
}

/** The label a container carries: `aria-label`, or `aria-labelledby` resolved to its text. */
function containerLabel(el: Element): string {
  const ariaLabel = normalizeText(el.getAttribute("aria-label"));
  if (ariaLabel) return ariaLabel;
  const labelledby = normalizeText(el.getAttribute("aria-labelledby"));
  if (!labelledby) return "";
  const doc = el.ownerDocument;
  return normalizeText(
    labelledby
      .split(" ")
      .map((id) => doc?.getElementById(id)?.textContent ?? "")
      .join(" "),
  );
}

function isChipContainerShaped(el: Element): boolean {
  const role = normalizeText(el.getAttribute("role")).toLowerCase();
  if (role === "listbox" || role === "list") return true;
  const tag = el.tagName.toUpperCase();
  return tag === "UL" || tag === "OL";
}

/** The chip nodes one container holds, outermost only, so a wrapped chip is not read twice. */
function chipNodesOf(list: Element): Element[] {
  const found = Array.from(list.querySelectorAll(CHIP_NODE_SELECTOR));
  return found.filter((node) => {
    const parent = node.parentElement;
    if (!parent || !list.contains(parent)) return true;
    return parent.closest(CHIP_NODE_SELECTOR) === null || !list.contains(parent.closest(CHIP_NODE_SELECTOR));
  });
}

/** A control inside the chip whose accessible name reads as "Remove …". */
function removeControlOf(chip: Element): Element | null {
  const controls = Array.from(chip.querySelectorAll("button, [role='button'], a[href]"));
  for (const control of controls) {
    const name = normalizeText(control.getAttribute("aria-label") ?? control.textContent ?? "");
    if (REMOVE_NAME.test(name)) return control;
  }
  for (const control of controls) {
    const label = normalizeText(control.getAttribute("aria-label"));
    if (label && ADDRESS.test(normalizeText(label.replace(LEADING_VERB, "")))) return control;
  }
  return null;
}

/** The accessible name of a control, the way the recorder reads one. */
function controlName(control: Element): string {
  const ariaLabel = normalizeText(control.getAttribute("aria-label"));
  if (ariaLabel) return ariaLabel;
  return normalizeText(control.textContent);
}

/** The addresses a piece of text holds, in order. */
function addressTokens(text: string): string[] {
  return text.split(/\s+/).filter((token) => ADDRESS.test(token));
}

/**
 * One chip's value, in the plan's §1.3 order, first match wins:
 *
 *   1. the first attribute whose value parses as an address (any attribute, never a fixed list);
 *   2. the remove control's name with a leading verb stripped, when what remains is an address;
 *   3. the chip's own text when it holds exactly one address-shaped token;
 *   4. otherwise the chip's text — a display name is the last resort, never the first.
 *
 * The display name is therefore never chosen while an address-shaped value exists anywhere on the
 * chip, which is the rule the ticket names.
 */
export function chipValueOf(node: Element): string | null {
  for (const attribute of Array.from(node.attributes)) {
    const value = normalizeText(attribute.value);
    if (value.includes("@") && ADDRESS.test(value)) return value;
  }

  const remove = removeControlOf(node);
  if (remove) {
    const stripped = normalizeText(controlName(remove).replace(LEADING_VERB, ""));
    if (ADDRESS.test(stripped)) return stripped;
  }

  const text = normalizeText(node.textContent);
  const tokens = addressTokens(text);
  if (tokens.length === 1) return tokens[0]!;
  return text || null;
}

/** Whether a chip carries an address-shaped value, by any of the plan's three identity clauses. */
function chipIsAddressShaped(node: Element): boolean {
  const attribute = Array.from(node.attributes).some((entry) => {
    const value = normalizeText(entry.value);
    return value.includes("@") && ADDRESS.test(value);
  });
  if (attribute) return true;
  const remove = removeControlOf(node);
  if (remove && ADDRESS.test(normalizeText(controlName(remove).replace(LEADING_VERB, "")))) return true;
  return addressTokens(normalizeText(node.textContent)).length === 1;
}

/** Whether a container is a chip list: container-shaped, labelled, and not a combobox popup. */
function isChipList(list: Element): boolean {
  if (!isChipContainerShaped(list)) return false;
  if (!containerLabel(list)) return false;
  // A listbox a combobox points at is that combobox's suggestion popup, never a chip list.
  if (controllerOf(list)) return false;
  // A popup whose options are neither addresses nor paired with a remove control is a popup too,
  // even when the widget declares no `aria-controls`.
  const options = Array.from(list.querySelectorAll("[role='option']"));
  if (options.length > 0 && !options.some((option) => chipIsAddressShaped(option) || removeControlOf(option) !== null)) {
    return false;
  }
  return true;
}

/** The chip values a container holds: only address-shaped chips contribute. */
export function chipValuesOf(list: Element): string[] {
  const values: string[] = [];
  for (const node of chipNodesOf(list)) {
    if (!chipIsAddressShaped(node)) continue;
    const value = chipValueOf(node);
    if (!value || values.includes(value)) continue;
    values.push(value);
  }
  return values;
}

/** The enclosing chip list of an element, if it sits inside one. */
export function chipListAncestor(el: Element): Element | null {
  for (let node: Element | null = el; node; node = node.parentElement) {
    if (node.matches(CHIP_CONTAINER_SELECTOR) && isChipList(node)) return node;
  }
  return null;
}

/** The closest chip node an element sits in, if any. */
export function chipAncestorOf(el: Element): Element | null {
  for (let node: Element | null = el; node; node = node.parentElement) {
    if (node.matches(CHIP_NODE_SELECTOR)) return node;
  }
  return null;
}

function isTextLike(el: Element): boolean {
  const tag = el.tagName.toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA") return true;
  if (isRichText(el)) return true;
  return el.matches("[role='textbox']");
}

/** Whether this element's value must never be read. Checked before any read, always. */
function isSecret(el: Element): boolean {
  return isPasswordField(el) || (isRichText(el) && isCredentialContext(el));
}

function isSkipped(el: Element): boolean {
  if (isSecret(el)) return true;
  const tag = el.tagName.toUpperCase();
  if (tag === "INPUT") {
    const type = (el.getAttribute("type") ?? "text").toLowerCase();
    if (type === "hidden" || type === "file" || type === "password") return true;
  }
  return !isVisibleElement(el);
}

/** A nested contenteditable is contained by its root's text, so only the root is read. */
function hasRichTextAncestor(el: Element): boolean {
  const parent = el.parentElement;
  if (!parent) return false;
  return isRichText(parent);
}

function readEntry(el: Element): SnapshotEntry | null {
  if (el.matches(CHIP_CONTAINER_SELECTOR) && isChipList(el)) {
    const chipValues = chipValuesOf(el);
    // A container with no chip values at all is not an entry, and is never read as a text field.
    if (chipValues.length === 0) return null;
    return {
      kind: "chip",
      value: chipValues.join("\n"),
      chipValues,
    };
  }

  const tag = el.tagName.toUpperCase();
  if (tag === "SELECT") {
    const select = el as HTMLSelectElement;
    const option = select.selectedOptions?.[0] ?? select.options?.[select.selectedIndex ?? -1];
    return {
      kind: "select",
      value: normalizeText(option?.textContent ?? select.value),
      chipValues: [],
    };
  }

  if (!isTextLike(el)) return null;
  if (el.matches("[contenteditable]") && hasRichTextAncestor(el)) return null;
  const value = isRichText(el)
    ? normalizeText(el.textContent)
    : ((el as HTMLInputElement | HTMLTextAreaElement).value ?? "");
  return {
    kind: "text",
    value,
    chipValues: [],
  };
}

function gatherCandidates(
  root: ParentNode,
  out: Element[],
  limit: number,
  state: WalkState,
): void {
  const candidates = Array.from(root.querySelectorAll(CANDIDATE_SELECTOR));
  for (const el of candidates) {
    if (out.length >= limit) {
      state.truncated = true;
      return;
    }
    out.push(el);
  }

  // An open shadow root hangs off *any* element, not only off a candidate: a custom element that
  // wraps its own input is the ordinary case. Hosts are therefore found by their shadowRoot.
  for (const el of Array.from(root.querySelectorAll("*"))) {
    if (state.truncated) return;
    const shadow = (el as Element & { shadowRoot?: ShadowRoot | null }).shadowRoot ?? null;
    if (shadow) gatherCandidates(shadow, out, limit, state);
  }

  for (const frame of Array.from(root.querySelectorAll("iframe"))) {
    if (state.truncated) return;
    let inner: Document | null = null;
    try {
      inner = (frame as HTMLIFrameElement).contentDocument;
    } catch {
      inner = null;
    }
    if (!inner) {
      // A cross-origin frame is not ours to read. It is skipped silently, and remembered.
      state.crossOrigin = true;
      continue;
    }
    gatherCandidates(inner, out, limit, state);
  }
}

/**
 * One pass over the document: the fields, selects and chip lists that survive the password,
 * hidden, file and visibility rules, each with its current value and target ladder.
 */
export function collectSnapshot(doc: Document, limit = SNAPSHOT_LIMIT): Snapshot {
  const state: WalkState = { truncated: false, crossOrigin: false };
  const candidates: Element[] = [];
  gatherCandidates(doc, candidates, limit, state);

  const entries = new Map<Element, SnapshotEntry>();
  for (const el of candidates) {
    if (isSkipped(el)) continue;
    const entry = readEntry(el);
    if (entry) entries.set(el, entry);
  }
  return { entries, document: doc, truncated: state.truncated };
}

function valuesOf(entry: SnapshotEntry | undefined): string[] {
  return entry?.kind === "chip" ? entry.chipValues : [];
}

/**
 * What changed between two passes, as the shapes the recorder turns into steps.
 *
 * A field that changed and changed back is identical to its baseline and produces nothing; an
 * empty value is never a fill; a chip is diffed by value, so a re-render that keeps the same
 * recipients is not a change.
 */
export function diffSnapshot(before: Snapshot, after: Snapshot): SnapshotDiff {
  const fills: SnapshotDiff["fills"] = [];
  const selects: SnapshotDiff["selects"] = [];
  const chipsAdded: SnapshotDiff["chipsAdded"] = [];
  const chipsRemoved: SnapshotDiff["chipsRemoved"] = [];

  for (const [element, entry] of after.entries) {
    const previous = before.entries.get(element);
    if (entry.kind === "text") {
      if (!entry.value) continue;
      if (previous && previous.value === entry.value) continue;
      fills.push({ element, value: entry.value, entry });
      continue;
    }
    if (entry.kind === "select") {
      if (!entry.value) continue;
      // An appearing select is not a change: a native select already has a selection when the
      // page renders it, and reading that as a step would record a choice nobody made.
      if (!previous || previous.value === entry.value) continue;
      selects.push({ element, value: entry.value, entry });
    }
  }

  for (const [element, entry] of after.entries) {
    if (entry.kind !== "chip") continue;
    const beforeValues = valuesOf(before.entries.get(element));
    for (const value of entry.chipValues) {
      if (beforeValues.includes(value)) continue;
      chipsAdded.push({ list: element, value });
    }
  }

  for (const [element, entry] of before.entries) {
    if (entry.kind !== "chip") continue;
    const nowValues = valuesOf(after.entries.get(element));
    for (const value of entry.chipValues) {
      if (nowValues.includes(value)) continue;
      chipsRemoved.push({ list: element, value });
    }
  }

  return { fills, selects, chipsAdded, chipsRemoved };
}
