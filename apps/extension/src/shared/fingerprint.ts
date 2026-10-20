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

/**
 * The text of a node, omitting `<sup>` and `<sub>` children whose trimmed text is "?".
 *
 * Odoo renders `<label>Contact<sup>?</sup></label>`, which would give an accessible name and
 * label text of "Contact?". Dropping the help marker here keeps recordings readable and means
 * the compiled recipe does not have to strip it after the fact.
 */
function nodeTextExcludingHelp(node: Node | null | undefined): string {
  if (!node) return "";
  const pieces: string[] = [];
  const walk = (current: Node, skip: boolean) => {
    if (current.nodeType === Node.TEXT_NODE) {
      if (!skip) pieces.push(current.textContent ?? "");
      return;
    }
    if (current.nodeType !== Node.ELEMENT_NODE) return;
    const el = current as Element;
    const tag = el.tagName.toUpperCase();
    const childSkip =
      skip || ((tag === "SUP" || tag === "SUB") && normalizeText(el.textContent) === "?");
    for (const child of Array.from(el.childNodes)) {
      walk(child, childSkip);
    }
  };
  for (const child of Array.from(node.childNodes)) {
    walk(child, false);
  }
  return normalizeText(pieces.join(""));
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
  // A colon belongs to a minted id, never to a human one: Gmail's `:1a2`, React's `:r1:`.
  // A selector built from one would die on the next load, so it is not a rung.
  if (token.includes(":")) return false;
  // A run of four or more digits, or a numeric suffix, reads as generated: row-0042, makeStyles-root-12.
  if (/\d{4,}/.test(token)) return false;
  if (/-\d+$/.test(token)) return false;
  // A long hex run with no separator is a hash or a row id, not a name: a3f9c2b41d.
  if (/^[0-9a-f]{8,}$/.test(token)) return false;
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
  // The table trio. A search result is a cell, and `role: "cell"` is how the compiler names one
  // in a duplicate branch's condition; a recorder that called a cell `generic` would produce a rung
  // the executor agrees with but a person cannot read, and the two sides must agree exactly.
  TD: "cell",
  TH: "columnheader",
  TR: "row",
  // A LABEL is deliberately absent: `label` is a tag, not a role, and a click on one is
  // recorded against the control it labels (see the recorder's resolveClickTarget), so the
  // recorder never emits `role: "label"`. A label falls back to `generic`.
  H1: "heading",
  H2: "heading",
  H3: "heading",
  H4: "heading",
  H5: "heading",
  H6: "heading",
};

/**
 * The role spellings a recorded rung may carry: the recipe schema's role pattern
 * (``^[a-z][a-z0-9-]*$``). The resolver compares a rung's role literally, so a role
 * that cannot be spelled that way is a rung that can never match — the recorder drops
 * it and falls back to the element's tag.
 */
const ROLE_PATTERN = /^[a-z][a-z0-9-]*$/;

/** The element's ARIA role: an explicit `role`, else the implicit role of its tag. */
export function semanticRole(el: Element): string {
  const explicit = normalizeText(el.getAttribute("role"));
  if (explicit) {
    const first = explicit.split(" ")[0];
    if (first) {
      const role = first.toLowerCase();
      if (role !== "label" && ROLE_PATTERN.test(role)) return role;
    }
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
  return label ? nodeTextExcludingHelp(label) : "";
}

/** Elements whose text is a glyph or a control affordance, never part of a column name. */
const HEADER_GLYPH_SELECTOR =
  "i, svg, [aria-hidden='true'], .o_list_sortable_caret, .o_resize, .o_list_header_label_spacer";

const CELL_ROLES = new Set(["cell", "gridcell"]);
const HEADER_CELL_ROLES = new Set(["columnheader"]);

function isCellLike(el: Element): boolean {
  const tag = el.tagName.toUpperCase();
  if (tag === "TD") return true;
  return CELL_ROLES.has((el.getAttribute("role") ?? "").trim().toLowerCase());
}

function isHeaderCellLike(el: Element): boolean {
  const tag = el.tagName.toUpperCase();
  if (tag === "TH") return true;
  return HEADER_CELL_ROLES.has((el.getAttribute("role") ?? "").trim().toLowerCase());
}

function indexAttribute(el: Element, name: string): number | null {
  const raw = normalizeText(el.getAttribute(name));
  return /^\d+$/.test(raw) ? parseInt(raw, 10) : null;
}

/**
 * The name of the column header a table cell belongs to, or "" when the element does not sit
 * in one. A control inside a `td` (or `role=cell`/`role=gridcell`) that carries no label of
 * its own is called by its column: the `th` (or `role=columnheader`) at the same column
 * position, counting `colspan` (`aria-colspan` in a grid), or the column named by the cell's
 * `aria-colindex`. The header's text is trimmed, with sort arrows and icon glyphs left out.
 *
 * This is PL-048: Odoo's editable order-line fields have no accessible name at all -- only a
 * placeholder, or nothing -- so the recorder had nothing better than rungs that cannot resolve
 * (PL-046). The column header is what a person calls those fields.
 *
 * The resolver (`accessibleNameOf`/role rungs in `executor/ladder.ts`) cannot import this: it
 * is serialized into the page as a self-contained function. It mirrors this rule, and
 * `tests/order-line-names.test.ts` runs the same elements through both sides to pin the
 * agreement.
 */
export function columnHeaderName(el: Element): string {
  const cell =
    el.parentElement && isCellLike(el)
      ? el
      : el.closest?.("td, [role='cell'], [role='gridcell']");
  if (!cell) return "";
  const table = cell.closest?.("table, [role='table'], [role='grid']");
  if (!table) return "";

  // The column the cell sits in: its own aria-colindex, else its position among the row's
  // cells. The leading handle/selector column of Odoo's lists is a cell like any other, so
  // positions line up with the header row's positions.
  let column: number | null = null;
  const colIndex = indexAttribute(cell, "aria-colindex");
  if (colIndex !== null) {
    column = colIndex - 1;
  } else {
    const row = cell.parentElement;
    if (!row) return "";
    const cells = Array.from(row.children).filter(isCellLike);
    column = cells.indexOf(cell);
  }
  if (column === null || column < 0) return "";

  // The header row: thead's first row, else the first row of the table/grid.
  const thead = table.querySelector("thead");
  const headerRow =
    thead?.children[0] ??
    (table.tagName.toUpperCase() === "TR" ? table : table.querySelector("tr, [role='row']"));
  if (!headerRow) return "";

  // Walk the header cells to the one covering `column`: each carries its own aria-colindex,
  // else it starts where the previous one ended, spanning colspan (aria-colspan in a grid).
  let cursor = 0;
  let header: Element | null = null;
  for (const candidate of Array.from(headerRow.children).filter(isHeaderCellLike)) {
    const own = indexAttribute(candidate, "aria-colindex");
    const spanValue = indexAttribute(candidate, "aria-colspan");
    const start = own !== null ? own - 1 : cursor;
    const span = Math.max(
      1,
      own !== null ? (spanValue ?? 1) : (indexAttribute(candidate, "colspan") ?? 1),
    );
    if (column >= start && column < start + span) {
      header = candidate;
      break;
    }
    cursor = start + span;
  }
  if (!header) return "";

  // Header text, trimmed, with sort arrows and icon glyphs left out: Odoo's caret is an <i>
  // (its glyph is a font, but a page can put a character in one), and a resize handle or an
  // aria-hidden decoration is never part of the name.
  const clone = header.cloneNode(true) as Element;
  for (const glyph of Array.from(clone.querySelectorAll(HEADER_GLYPH_SELECTOR))) {
    glyph.remove();
  }
  return normalizeText(clone.textContent);
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
      .map((id) => nodeTextExcludingHelp(root.getElementById(id)))
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
    // A field inside a table cell with no label of its own is named by its column header,
    // BEFORE the placeholder (PL-048): the product cell of an Odoo order line then records as
    // role combobox, name "Product", and the quantity as "Quantity", instead of rungs that
    // cannot resolve. The resolver accepts the header name as well as the placeholder, so a
    // recipe pinned with the placeholder name keeps resolving.
    const header = columnHeaderName(el);
    if (header) return truncate(header, 200);
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

  // The author's own `title` outranks the element's text. The text may be a glyph — the ✕
  // on a recipient chip — and for a rich-text body it is the value the person just typed,
  // which must never become part of a target. The resolver computes the name the same way
  // (`accessibleNameOf` in `executor/ladder.ts`), so a recorded rung still matches at run time.
  const title = normalizeText(el.getAttribute("title"));
  if (title) return truncate(title, 200);

  if (isRichText(el)) return "";

  const text = nodeTextExcludingHelp(el);
  return text ? truncate(text, 200) : "";
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
 * Whether every remaining rung on the ladder is merely positional.
 *
 * A positional path — `button`, `div > span:nth-of-type(2)` — says where an element sat,
 * not what it is, so it survives nothing: the next layout change moves it and the step can
 * no longer resolve. A `css` rung is durable only when its selector names something the
 * page owns — an id, a class, or an attribute — which is why stability is read from `#`,
 * `.` or `[` in the selector. A ladder left with no durable rung at all (including an
 * empty one) is positional-only too; there is nothing better to fall back on either way.
 */
export function hasOnlyPositionalTargets(targets: LocatorRung[]): boolean {
  return !targets.some((rung) => {
    if (rung.by === "role" || rung.by === "testid" || rung.by === "label") return true;
    if (rung.by === "css") return /[#.\[]/.test(rung.selector);
    return false;
  });
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
  // A rich-text body's text is its value, not its label: the same reason a text field's
  // `value` is never returned here.
  if (isRichText(el)) {
    return truncate(
      normalizeText(el.getAttribute("placeholder")) ||
        normalizeText(el.getAttribute("aria-label")) ||
        normalizeText(el.getAttribute("title")),
      200,
    );
  }
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
  if (autocomplete.includes("password")) return true;
  // PL-068: a one-time verification code is a credential too. The run asks the person for it on
  // the run page; the recording must never hold one a person typed while teaching.
  if (autocomplete.includes("one-time-code")) return true;
  const named = `${el.getAttribute("name") ?? ""} ${el.getAttribute("id") ?? ""}`.toLowerCase();
  return ONE_TIME_CODE_NAME.test(named);
}

/** Names and ids a one-time-code field carries: otp, mfa/2fa codes, verification codes. */
const ONE_TIME_CODE_NAME =
  /\b(otp|one[-_ ]?time[-_ ]?(code|password)|(verification|mfa|2fa|totp|auth)[-_ ]?code)\b/;

/** True for an element the person types into directly, without a value property. */
export function isRichText(el: Element): boolean {
  const own = (el.getAttribute("contenteditable") ?? "").trim().toLowerCase();
  if (own === "true" || own === "plaintext-only") return true;
  return el.closest?.("[contenteditable='true'], [contenteditable='plaintext-only']") !== null;
}

function autocompleteOff(el: Element): boolean {
  return (el.getAttribute("autocomplete") ?? "").trim().toLowerCase() === "off";
}

/**
 * True for a value that must never be captured beyond the password rule itself.
 *
 * The password rule is the base case (an input the browser will not even fill for us).
 * A rich-text body has no `type`, so it is judged by the context it sits in: a container
 * that holds a password field, or a context the page marked `autocomplete="off"`, is a
 * credential form, and a note typed inside one is as private as the fields next to it.
 */
export function isCredentialContext(el: Element): boolean {
  if (isPasswordField(el)) return true;
  let node: Element | null = el;
  while (node) {
    if (autocompleteOff(node)) return true;
    if (node.matches?.("form, [role='form'], [role='dialog']")) {
      return node.querySelector("input[type='password']") !== null;
    }
    node = node.parentElement;
  }
  return false;
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
