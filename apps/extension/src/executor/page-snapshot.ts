// PL-122: the page snapshot a run sends when the remembered target is gone.
//
// This is the only page reading a model may see. It is written as one self-contained function so
// the executor can serialize it into the target page, exactly like the ladder resolver. The rules
// it enforces are the ones the ticket names:
//
//   * a password field, a hidden input and a typed value are never read;
//   * a page whose origin is not allowed yields nothing at all;
//   * iframes are not entered (a cross-origin frame is not ours, and a token must resolve in the
//     document the resolver runs in);
//   * the snapshot is capped, nearest the viewport first, and every element gets a short
//     `data-pl-snap` token the `snap` rung can resolve.
import type { ResolveRung } from "./ladder";

/** How many elements one snapshot may carry. */
export const SNAPSHOT_LIMIT = 400;

/** How much visible text one element may contribute. */
export const SNAPSHOT_TEXT_LIMIT = 120;

export interface PageSnapshotElement {
  token: string;
  role: string;
  name: string;
  label: string;
  placeholder: string;
  text: string;
  section: string;
  visible: boolean;
  enabled: boolean;
  tag: string;
  ladder: ResolveRung[];
}

export interface PageSnapshotResult {
  ok: boolean;
  origin: string;
  elements: PageSnapshotElement[];
  truncated: boolean;
  reason?: string;
}

export interface PageSnapshotQuery {
  /** The origins the step may act on. An empty list means no origin restriction. */
  allowedOrigins?: string[];
  /** Defaults to {@link SNAPSHOT_LIMIT}. */
  limit?: number;
  /**
   * PL-123: this step acts on a search result. The page's search box and everything inside it --
   * the facet chips that show the query back -- are left out of the snapshot entirely, so a model
   * asked for a result can never be offered the chip that carries the same words.
   */
  excludeSearchBox?: boolean;
}

export function pageSnapshotInPage(query: PageSnapshotQuery): PageSnapshotResult {
  const LIMIT = typeof query.limit === "number" && query.limit > 0 ? query.limit : 400;
  const TEXT_LIMIT = 120;
  const allowed = Array.isArray(query.allowedOrigins) ? query.allowedOrigins : [];
  let origin = "";
  try {
    origin = String(location.origin || "");
  } catch {
    origin = "";
  }
  if (allowed.length > 0 && !allowed.includes(origin)) {
    return {
      ok: false,
      origin,
      elements: [],
      truncated: false,
      reason: "the page moved off the allowed origin",
    };
  }

  const doc = document;
  const norm = (value: unknown): string =>
    value === null || value === undefined ? "" : String(value).replace(/\s+/g, " ").trim();
  const clip = (value: string): string =>
    value.length > TEXT_LIMIT ? `${value.slice(0, TEXT_LIMIT)}…` : value;

  /** Every element under a root, open shadow roots included. Iframes are deliberately not entered. */
  const deepElements = (root: Document | ShadowRoot | Element): Element[] => {
    const out: Element[] = [];
    const walk = (node: Document | ShadowRoot | Element): void => {
      for (const el of Array.from(node.querySelectorAll("*"))) {
        out.push(el);
        const shadow = (el as Element & { shadowRoot?: ShadowRoot | null }).shadowRoot;
        if (shadow) walk(shadow);
      }
    };
    walk(root);
    return out;
  };

  const all = deepElements(doc);

  const visibleOf = (el: Element): boolean => {
    if (el.hasAttribute("hidden")) return false;
    const check = (el as Element & { checkVisibility?: () => boolean }).checkVisibility;
    if (typeof check === "function") {
      try {
        if (check.call(el) === false) return false;
      } catch {
        // A page that refuses checkVisibility falls through to the style test.
      }
    }
    const view = doc.defaultView;
    const style = view && typeof view.getComputedStyle === "function" ? view.getComputedStyle(el) : null;
    if (style && (style.display === "none" || style.visibility === "hidden")) return false;
    for (let parent = el.parentElement; parent; parent = parent.parentElement) {
      if (parent.hasAttribute("hidden")) return false;
    }
    return true;
  };

  const enabledOf = (el: Element): boolean => {
    const field = el as HTMLInputElement & { disabled?: boolean };
    if (field.disabled === true) return false;
    if (norm(el.getAttribute("aria-disabled")).toLowerCase() === "true") return false;
    if (norm(el.getAttribute("aria-busy")).toLowerCase() === "true") return false;
    return true;
  };

  const roleOf = (el: Element): string => {
    const explicit = norm(el.getAttribute("role"));
    if (explicit) {
      const first = explicit.split(" ")[0];
      if (first) return first.toLowerCase();
    }
    const tag = el.tagName.toUpperCase();
    if (tag === "INPUT") {
      const type = (el.getAttribute("type") ?? "text").toLowerCase();
      const map: Record<string, string> = {
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
      return map[type] ?? "textbox";
    }
    const tags: Record<string, string> = {
      A: "link",
      BUTTON: "button",
      SELECT: "combobox",
      TEXTAREA: "textbox",
      IMG: "img",
      UL: "list",
      OL: "list",
      LI: "listitem",
      TABLE: "table",
      TD: "cell",
      TH: "columnheader",
      TR: "row",
      H1: "heading",
      H2: "heading",
      H3: "heading",
      H4: "heading",
      H5: "heading",
      H6: "heading",
      MAIN: "main",
      NAV: "navigation",
      FORM: "form",
      DIALOG: "dialog",
    };
    if (tag === "A" && !el.hasAttribute("href")) return "generic";
    return tags[tag] ?? "generic";
  };

  const textOf = (el: Element): string => {
    const tag = el.tagName.toUpperCase();
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return "";
    if (el.getAttribute("contenteditable") === "true") return "";
    if (el.getAttribute("contenteditable") === "plaintext-only") return "";
    // Walk the text nodes, skipping every form value. An ancestor's `textContent` includes a
    // textarea's typed text, and that text must never reach a model through the parent card.
    const pieces: string[] = [];
    const walk = (node: Node): void => {
      if (node.nodeType === 3) {
        pieces.push(node.textContent ?? "");
        return;
      }
      if (node.nodeType !== 1) return;
      const child = node as Element;
      const childTag = child.tagName.toUpperCase();
      if (["INPUT", "SELECT", "TEXTAREA", "SCRIPT", "STYLE"].includes(childTag)) return;
      const editable = child.getAttribute("contenteditable");
      if (editable === "true" || editable === "plaintext-only") return;
      for (const grandchild of Array.from(child.childNodes)) walk(grandchild);
    };
    for (const child of Array.from(el.childNodes)) walk(child);
    return clip(norm(pieces.join("")));
  };

  const labelOf = (el: Element): string => {
    const id = el.getAttribute("id");
    if (id) {
      for (const root of [el.getRootNode(), doc]) {
        const label = (root as ParentNode).querySelector?.(`label[for="${id.replace(/["\\]/g, "\\$&")}"]`);
        if (label) return clip(norm(label.textContent));
      }
    }
    const wrapping = el.closest("label");
    return wrapping ? clip(norm(wrapping.textContent)) : "";
  };

  const nameOf = (el: Element): string => {
    const labelledby = norm(el.getAttribute("aria-labelledby"));
    if (labelledby) {
      const parts = labelledby
        .split(" ")
        .map((id) => doc.getElementById(id)?.textContent ?? "")
        .join(" ");
      const text = norm(parts);
      if (text) return clip(text);
    }
    const ariaLabel = norm(el.getAttribute("aria-label"));
    if (ariaLabel) return clip(ariaLabel);
    const label = labelOf(el);
    if (label) return label;
    const tag = el.tagName.toUpperCase();
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") {
      const placeholder = norm(el.getAttribute("placeholder"));
      if (placeholder) return clip(placeholder);
    }
    const alt = norm(el.getAttribute("alt"));
    if (alt) return clip(alt);
    const title = norm(el.getAttribute("title"));
    if (title) return clip(title);
    // A container's own name is its first run of text, not every price and badge it holds. The
    // card in a product list is named by the product, not by "Oak Standing Desk 799".
    const firstChild = el.firstElementChild ? norm(el.firstElementChild.textContent) : "";
    return clip(firstChild || textOf(el));
  };

  const headings = all.filter(
    (el) => /^H[1-6]$/.test(el.tagName.toUpperCase()) || norm(el.getAttribute("role")) === "heading",
  );
  const sectionOf = (el: Element): string => {
    for (const heading of headings) {
      if (heading === el) continue;
      // 4 is Node.DOCUMENT_POSITION_FOLLOWING: the heading comes before the element.
      if (heading.compareDocumentPosition(el) & 4) {
        const text = clip(norm(heading.textContent));
        if (text) return text;
      }
    }
    return "";
  };

  const cssEscape = (value: string): string => value.replace(/[^A-Za-z0-9_-]/g, (ch) => `\\${ch}`);
  const stableSelector = (el: Element): string => {
    if (el.id) {
      const selector = `#${cssEscape(el.id)}`;
      try {
        if (doc.querySelectorAll(selector).length === 1) return selector;
      } catch {
        // An id that is not a legal selector falls through to the path.
      }
    }
    const testid = norm(el.getAttribute("data-testid") ?? el.getAttribute("data-test"));
    if (testid) return `[data-testid="${testid.replace(/["\\]/g, "\\$&")}"]`;
    const parts: string[] = [];
    let node: Element | null = el;
    while (node && node !== doc.body && parts.length < 4) {
      const current: Element = node;
      const tag = current.tagName.toLowerCase();
      const parent: Element | null = current.parentElement;
      if (!parent) {
        parts.unshift(tag);
        break;
      }
      const siblings = Array.from(parent.children).filter(
        (child) => child.tagName === current.tagName,
      );
      parts.unshift(`${tag}:nth-of-type(${siblings.indexOf(current) + 1})`);
      node = parent;
    }
    return parts.join(" > ");
  };

  const ladderOf = (el: Element, role: string, name: string, label: string): ResolveRung[] => {
    const ladder: ResolveRung[] = [];
    if (role && name) ladder.push({ by: "role", role, name });
    if (label) ladder.push({ by: "label", text: label });
    const selector = stableSelector(el);
    if (selector) ladder.push({ by: "css", selector });
    return ladder.slice(0, 5);
  };

  const isSecret = (el: Element): boolean => {
    const tag = el.tagName.toUpperCase();
    if (tag !== "INPUT") return false;
    const type = (el.getAttribute("type") ?? "text").toLowerCase();
    return type === "password" || type === "hidden" || type === "file";
  };

  /**
   * PL-123: whether this element is the search box, or a chip inside it.
   *
   * A search shows the typed text twice -- once inside the box as a facet chip, once on the
   * result. For a step that means a result, the box and its chips are not candidates at all.
   * Only ancestors are judged, so a result container that happens to hold a search box is kept.
   */
  const insideSearchBox = (el: Element): boolean => {
    for (let node: Element | null = el; node; node = node.parentElement) {
      const tag = node.tagName.toUpperCase();
      if (tag === "INPUT" && (node.getAttribute("type") ?? "text").toLowerCase() === "search") {
        return true;
      }
      if (tag === "FORM" && (node.getAttribute("role") ?? "").toLowerCase() === "search") {
        return true;
      }
      const role = (node.getAttribute("role") ?? "").toLowerCase();
      if (role === "search" || role === "searchbox") return true;
      const classes = (node.getAttribute("class") ?? "").toLowerCase();
      if (/(^|[\s_-])o_searchview|search[-_]?(bar|box|field|view|input|chips?)([\s_-]|$)/.test(classes)) {
        return true;
      }
      if (/(^|[\s_-])o_facet([\s_-]|$)/.test(classes)) return true;
    }
    return false;
  };
  const excludeSearch = query.excludeSearchBox === true;

  const isInteractive = (el: Element): boolean => {
    const tag = el.tagName.toUpperCase();
    if (tag === "A" && el.hasAttribute("href")) return true;
    if (tag === "BUTTON" || tag === "SELECT" || tag === "TEXTAREA") return true;
    if (tag === "INPUT") return true;
    if (el.getAttribute("contenteditable") === "true") return true;
    const role = roleOf(el);
    if (
      [
        "button",
        "link",
        "textbox",
        "searchbox",
        "combobox",
        "listbox",
        "checkbox",
        "radio",
        "switch",
        "tab",
        "menuitem",
        "option",
        "listitem",
        "cell",
        "row",
        "article",
      ].includes(role)
    ) {
      return true;
    }
    const tabindex = norm(el.getAttribute("tabindex"));
    return tabindex !== "" && tabindex !== "-1";
  };

  const candidates: { el: Element; index: number; distance: number }[] = [];
  // A pathological page can hold tens of thousands of elements. The snapshot is capped anyway, so
  // stop collecting once there is enough to fill it several times over; the walk is the expensive
  // part on a large app like Odoo.
  const SCAN_LIMIT = 2000;
  let scannedOut = false;
  for (const [index, el] of all.entries()) {
    const tag = el.tagName.toUpperCase();
    if (
      ["HTML", "BODY", "SCRIPT", "STYLE", "META", "LINK", "HEAD", "TITLE", "NOSCRIPT", "TEMPLATE"].includes(
        tag,
      )
    ) {
      continue;
    }
    if (isSecret(el)) continue;
    if (excludeSearch && insideSearchBox(el)) continue;
    const name = nameOf(el);
    const role = roleOf(el);
    const text = textOf(el);
    if (!isInteractive(el) && !name && !text) continue;
    if (!name && !text && (role === "generic" || role === "")) continue;

    const rect = el.getBoundingClientRect();
    const hasBox = rect.width > 0 || rect.height > 0;
    const distance = hasBox
      ? Math.abs(rect.top - window.innerHeight / 2) + Math.abs(rect.left - window.innerWidth / 2)
      : 1_000_000 + index;
    candidates.push({ el, index, distance });
    if (candidates.length >= SCAN_LIMIT) {
      scannedOut = true;
      break;
    }
  }

  candidates.sort((a, b) => a.distance - b.distance || a.index - b.index);
  const truncated = scannedOut || candidates.length > LIMIT;
  const selected = candidates.slice(0, LIMIT);

  // Clear last snapshot's tokens, in every open shadow root, so a stale token can never resolve.
  for (const el of deepElements(doc)) el.removeAttribute("data-pl-snap");

  const elements: PageSnapshotElement[] = selected.map(({ el }, index) => {
    const token = `e${index + 1}`;
    el.setAttribute("data-pl-snap", token);
    const role = roleOf(el);
    const name = nameOf(el);
    const label = labelOf(el);
    const placeholder = norm(el.getAttribute("placeholder"));
    return {
      token,
      role,
      name,
      label,
      placeholder: clip(placeholder),
      text: textOf(el),
      section: sectionOf(el),
      visible: visibleOf(el),
      enabled: enabledOf(el),
      tag: el.tagName.toLowerCase(),
      ladder: ladderOf(el, role, name, label),
    };
  });

  return { ok: true, origin, elements, truncated };
}

/** The page function as an injectable expression, for `Runtime.evaluate`. */
export function snapshotExpression(): string {
  return `(${pageSnapshotInPage.toString()})`;
}
