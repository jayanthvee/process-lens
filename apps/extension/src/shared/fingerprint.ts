// Semantic fingerprints: describe an element by what it means, not where it sat.
//
// This builds the ordered target ladder the recipe format expects — accessible
// role and name inside a named container, then a test id, then the label, then a
// stable CSS selector, then fuzzy text — so a recorded step survives a page that
// regenerates its markup.
import type { FrameStep, LocatorRung } from "./recipe-types";

const CONTAINER_TAGS = ["FORM", "DIALOG", "FIELDSET", "SECTION"];
const CONTAINER_ROLES = ["dialog", "form", "region", "search", "group"];

/** Collapse whitespace and trim, so fingerprints compare equal across markup. */
export function normalizeText(value: string | null | undefined): string {
  return (value ?? "").replace(/\s+/g, " ").trim();
}

function truncate(value: string, limit: number): string {
  return value.length <= limit ? value : value.slice(0, limit);
}

/** Escape an identifier for use inside a CSS selector. */
function cssEscape(value: string): string {
  const globalCss = globalThis as { CSS?: { escape?: (input: string) => string } };
  if (typeof globalCss.CSS?.escape === "function") return globalCss.CSS.escape(value);
  return value.replace(/[^A-Za-z0-9_-]/g, (char) => `\\${char}`);
}

/**
 * Whether a class or id reads as a stable, human-written token rather than a
 * generated one. Hashed framework classes (css-1q2w3e, sc-bdfBQB) and tokens
 * with a run of digits are treated as unstable.
 */
export function isStableToken(token: string): boolean {
  if (!token || token.length > 40) return false;
  if (/^\d/.test(token)) return false;
  // A run of four or more digits, or a numeric suffix, reads as generated: row-0042, makeStyles-root-12.
  if (/\d{4,}/.test(token)) return false;
  if (/-\d+$/.test(token)) return false;
  // Framework prefixes with a long random suffix: sc-bdfBQB, css-1q2w3e.
  if (/^[a-z]{1,4}-[A-Za-z0-9]{6,}$/.test(token)) return false;
  // Letters and digits interleaved with no separator: a1b2c3d4e5.
  const digits = (token.match(/\d/g) ?? []).length;
  if (digits >= 3 && !/[-_]/.test(token)) return false;
  // Many internal capitals: aBcDeFgHiJ. A single one (customerSearch) is fine.
  const capitals = (token.match(/[a-z][A-Z]/g) ?? []).length;
  if (capitals >= 3) return false;
  return true;
}

const INPUT_ROLES: Record<string, string> = {
  button: "button",
  submit: "button",
  reset: "button",
  image: "button",
  checkbox: "checkbox",
  radio: "radio",
  range: "slider",
  number: "spinbutton",
  search: "searchbox",
  email: "textbox",
  tel: "textbox",
  text: "textbox",
  url: "textbox",
  password: "textbox",
  file: "button",
};

const TAG_ROLES: Record<string, string> = {
  A: "link",
  BUTTON: "button",
  SELECT: "combobox",
  TEXTAREA: "textbox",
  IMG: "img",
  TABLE: "table",
  UL: "list",
  OL: "list",
  LI: "listitem",
  NAV: "navigation",
  MAIN: "main",
  HEADER: "banner",
  FOOTER: "contentinfo",
  FORM: "form",
  DIALOG: "dialog",
  FIELDSET: "group",
  OPTION: "option",
  LABEL: "label",
  H1: "heading",
  H2: "heading",
  H3: "heading",
  H4: "heading",
  H5: "heading",
  H6: "heading",
};

/** The element's ARIA role: an explicit `role`, else the implicit role of its tag. */
export function semanticRole(el: Element): string {
  const explicit = normalizeText(el.getAttribute("role"));
  if (explicit) {
    const first = explicit.split(" ")[0];
    if (first) return first.toLowerCase();
  }
  const tag = el.tagName.toUpperCase();
  if (tag === "INPUT") {
    const type = (el.getAttribute("type") ?? "text").toLowerCase();
    return INPUT_ROLES[type] ?? "textbox";
  }
  if (tag === "A" && !el.hasAttribute("href")) return "generic";
  return (TAG_ROLES[tag] ?? "generic").toLowerCase();
}

function textOf(label: Element | null): string {
  return label ? normalizeText(label.textContent) : "";
}

/** The text of the `<label>` associated by `for` or by wrapping, if any. */
export function labelText(el: Element): string {
  const id = el.getAttribute("id");
  if (id) {
    const root = el.ownerDocument;
    const forLabel = root.querySelector(`label[for="${cssEscape(id)}"]`);
    const text = textOf(forLabel);
    if (text) return text;
  }
  const wrapping = el.closest("label");
  return textOf(wrapping);
}

function nameFromLabelledBy(el: Element, attr = "aria-labelledby"): string {
  const ids = normalizeText(el.getAttribute(attr));
  if (!ids) return "";
  const root = el.ownerDocument;
  return normalizeText(
    ids
      .split(" ")
      .map((id) => root.getElementById(id)?.textContent ?? "")
      .join(" "),
  );
}

/**
 * The accessible name: the value a person would use to refer to the control.
 * Follows the usual precedence — aria-labelledby, aria-label, an associated
 * label, then the visible content or a placeholder for inputs.
 */
export function accessibleName(el: Element): string {
  const labelledby = nameFromLabelledBy(el);
  if (labelledby) return truncate(labelledby, 200);

  const ariaLabel = normalizeText(el.getAttribute("aria-label"));
  if (ariaLabel) return truncate(ariaLabel, 200);

  const label = labelText(el);
  if (label) return truncate(label, 200);

  const isFieldControl =
    el.tagName === "INPUT" || el.tagName === "SELECT" || el.tagName === "TEXTAREA";
  if (isFieldControl) {
    const fromValue = el.tagName === "INPUT" ? submittableValue(el) : "";
    if (fromValue) return truncate(fromValue, 200);
    const placeholder = normalizeText(el.getAttribute("placeholder"));
    if (placeholder) return truncate(placeholder, 200);
    const title = normalizeText(el.getAttribute("title"));
    if (title) return truncate(title, 200);
    return "";
  }

  const alt = normalizeText(el.getAttribute("alt"));
  if (alt) return truncate(alt, 200);

  const text = normalizeText(el.textContent);
  if (text) return truncate(text, 200);
  const title = normalizeText(el.getAttribute("title"));
  return title ? truncate(title, 200) : "";
}

function submittableValue(el: Element): string {
  const tag = el.tagName.toUpperCase();
  if (tag !== "INPUT") return "";
  const type = (el.getAttribute("type") ?? "text").toLowerCase();
  if (type === "submit" || type === "button" || type === "reset") {
    return normalizeText((el as HTMLInputElement).value);
  }
  return "";
}

function isContainer(el: Element): boolean {
  if (CONTAINER_TAGS.includes(el.tagName.toUpperCase())) return true;
  const role = normalizeText(el.getAttribute("role")).toLowerCase();
  return CONTAINER_ROLES.includes(role);
}

function containerKind(el: Element): string {
  const role = normalizeText(el.getAttribute("role")).toLowerCase();
  if (role) return role;
  return el.tagName.toLowerCase();
}

function directChildOfTag(el: Element, tags: string[]): Element | null {
  for (const child of Array.from(el.children)) {
    if (tags.includes(child.tagName.toUpperCase())) return child;
  }
  return null;
}

function containerName(el: Element): string {
  const labelledby = nameFromLabelledBy(el);
  if (labelledby) return truncate(labelledby, 200);
  const ariaLabel = normalizeText(el.getAttribute("aria-label"));
  if (ariaLabel) return truncate(ariaLabel, 200);

  const legend = directChildOfTag(el, ["LEGEND"]);
  const legendText = textOf(legend);
  if (legendText) return truncate(legendText, 200);

  const heading = directChildOfTag(el, ["H1", "H2", "H3", "H4", "H5", "H6"]);
  const headingText = textOf(heading);
  if (headingText) return truncate(headingText, 200);

  const testId = normalizeText(
    el.getAttribute("data-testid") ?? el.getAttribute("data-test"),
  );
  return testId ? truncate(testId, 200) : "";
}

/**
 * The named container the element sits in (`form:New lead`), used to scope every
 * rung. Only a *named* container counts; an unnamed wrapper adds no scope.
 */
export function enclosingContainer(el: Element): string | undefined {
  let node: Element | null = el.parentElement;
  while (node) {
    if (isContainer(node)) {
      const name = containerName(node);
      if (name) return `${containerKind(node)}:${name}`;
    }
    node = node.parentElement;
  }
  return undefined;
}

/** `data-testid`, else `data-test`, trimmed. */
export function testIdOf(el: Element): string {
  return truncate(
    normalizeText(el.getAttribute("data-testid") ?? el.getAttribute("data-test")),
    200,
  );
}

function nthOfTypePath(el: Element, maxDepth = 4): string {
  const parts: string[] = [];
  let node: Element | null = el;
  let depth = 0;
  while (node && depth < maxDepth && node.tagName.toUpperCase() !== "BODY") {
    const tag = node.tagName.toLowerCase();
    if (node.id && isStableToken(node.id)) {
      parts.unshift(`#${cssEscape(node.id)}`);
      break;
    }
    const parent: Element | null = node.parentElement;
    if (!parent) {
      parts.unshift(tag);
      break;
    }
    const sameTag = Array.from(parent.children).filter(
      (child) => child.tagName === node!.tagName,
    );
    const ordinal = sameTag.indexOf(node) + 1;
    parts.unshift(sameTag.length > 1 ? `${tag}:nth-of-type(${ordinal})` : tag);
    node = parent;
    depth += 1;
  }
  return parts.join(" > ");
}

/** A short, stable CSS selector: a stable id, then stable classes, then a path. */
export function stableSelector(el: Element): string {
  const id = el.getAttribute("id");
  if (id && isStableToken(id)) return `#${cssEscape(id)}`;

  const tag = el.tagName.toLowerCase();
  const stableClasses = Array.from(el.classList).filter(isStableToken);
  if (stableClasses.length > 0) {
    return `${tag}.${stableClasses.slice(0, 3).map(cssEscape).join(".")}`;
  }

  const testId = testIdOf(el);
  if (testId) return `${tag}[data-testid="${cssEscape(testId)}"]`;

  return nthOfTypePath(el);
}

/**
 * The visible text a person would recognize the element by, as the last rung.
 *
 * A text field's `value` is deliberately never returned. It is the buffer the
 * person typed, not the element's own text; putting it in the ladder would bake
 * one record's data into the locator, and for a password field it would leak the
 * secret into the recording. `placeholder` and `aria-label` are the field's own
 * labels, so they stay. A `<select>` is a combobox, not a typed buffer, so its
 * current selection is still reported.
 */
export function fuzzyText(el: Element): string {
  const tag = el.tagName.toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA") {
    const placeholder = normalizeText(el.getAttribute("placeholder"));
    if (placeholder) return truncate(placeholder, 200);
    const ariaLabel = normalizeText(el.getAttribute("aria-label"));
    if (ariaLabel) return truncate(ariaLabel, 200);
    return "";
  }
  if (tag === "SELECT") {
    const placeholder = normalizeText(el.getAttribute("placeholder"));
    if (placeholder) return truncate(placeholder, 200);
    const ariaLabel = normalizeText(el.getAttribute("aria-label"));
    if (ariaLabel) return truncate(ariaLabel, 200);
    return truncate(normalizeText((el as HTMLSelectElement).value), 200);
  }
  const text = normalizeText(el.textContent);
  if (text) return truncate(text, 200);
  return truncate(normalizeText(el.getAttribute("title")), 200);
}

/**
 * The ordered target ladder for an element: best rung first, at most five rungs,
 * each rung distinct. This is the `targets` array of a recipe step.
 */
export function computeTargets(el: Element): LocatorRung[] {
  const within = enclosingContainer(el);
  const rungs: LocatorRung[] = [];

  const role = semanticRole(el);
  const name = accessibleName(el);
  if (role && name) {
    rungs.push(within ? { by: "role", role, name, within } : { by: "role", role, name });
  }

  const testId = testIdOf(el);
  if (testId) rungs.push({ by: "testid", value: testId });

  const label = labelText(el);
  if (label) rungs.push({ by: "label", text: truncate(label, 200) });

  const selector = stableSelector(el);
  if (selector) rungs.push({ by: "css", selector: truncate(selector, 500) });

  const text = fuzzyText(el);
  if (text) rungs.push({ by: "text_fuzzy", value: text });

  return rungs.slice(0, 5);
}

/**
 * True for a field whose value must never be captured: `type="password"`, or an
 * autocomplete hint that tells us the browser will treat it as a password.
 */
export function isPasswordField(el: Element): boolean {
  if (el.tagName.toUpperCase() !== "INPUT") return false;
  const type = (el.getAttribute("type") ?? "text").toLowerCase();
  if (type === "password") return true;
  const autocomplete = (el.getAttribute("autocomplete") ?? "").toLowerCase();
  return autocomplete.includes("password");
}

/**
 * The chain of iframes from the top document to the frame that holds this
 * document, outermost first. Undefined when the document is the top one.
 */
export function computeFramePath(win: Window = window): FrameStep[] | undefined {
  if (win === win.top) return undefined;
  const path: FrameStep[] = [];
  let current: Window = win;
  while (current !== current.top) {
    const frame: Element | null = current.frameElement;
    if (!frame?.parentElement) return undefined;
    const siblings = Array.from(frame.parentElement.children).filter(
      (child) => child.tagName === frame!.tagName,
    );
    path.unshift({ by: "index", index: Math.max(0, siblings.indexOf(frame)) });
    const parentWindow: Window | null = current.parent;
    if (!parentWindow) return undefined;
    current = parentWindow;
  }
  return path.length > 0 ? path : undefined;
}

/** A short human label for a step: "Click 'Save'". */
export function describeElement(el: Element): string {
  const name = accessibleName(el) || testIdOf(el) || el.tagName.toLowerCase();
  return truncate(name, 120);
}
