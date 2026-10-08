// The target resolution engine: the 5-rung ladder, climbing from the most
// semantic way of finding an element to the least.
//
// The resolver runs inside the target page, not in the extension. It is written
// as one self-contained function so it can be serialized with `.toString()` and
// evaluated in a page's context with `Runtime.evaluate`; the executor calls it
// through `resolveTarget`, and the unit tests call it directly against a
// document. It never reads or writes anything outside the page it runs in.
//
// A rung counts as found only when exactly one visible, enabled element matches
// it; an ambiguous rung is recorded and the ladder moves on, so a locator that
// could hit either of two elements can never be clicked.

/** The ladder, in the order the recipe format defines. */
export const LADDER: readonly string[] = ["role", "testid", "label", "css", "text_fuzzy"];

/** The attribute the resolver stamps on the element it found. */
export const TARGET_MARKER = "data-pl-target";

/** One rung of a target ladder, as stored in the recipe. */
export interface ResolveRung {
  by: string;
  role?: string;
  name?: string;
  value?: string;
  text?: string;
  selector?: string;
  threshold?: number;
  within?: string;
}

/** The query handed to the page-side resolver. */
export interface ResolveQuery {
  targets: ResolveRung[];
  /** Container scope for the whole bundle, e.g. "form:New lead". A rung overrides it. */
  within?: string | null;
  /** The token to stamp on the found element. */
  token: string;
}

export interface RungAttempt {
  rung: number;
  by: string;
  matches: number;
  reason?: string;
}

export interface ResolveResult {
  ok: boolean;
  rung: number;
  by: string;
  marker: string | null;
  /** The box center in CSS pixels, when the page has layout (0,0 in jsdom). */
  x: number;
  y: number;
  tag: string;
  attempts: RungAttempt[];
  error?: string;
}

/**
 * Find the element a target ladder describes. Runs in the page.
 *
 * `query.token` is stamped on the found element as `data-pl-target="<token>"`, so
 * every later CDP call (focus, clear, read) can address the same element without
 * re-climbing the ladder.
 */
export function resolveTargetInPage(query: ResolveQuery): ResolveResult {
  const LADDER = ["role", "testid", "label", "css", "text_fuzzy"];
  const MARKER = "data-pl-target";
  const attempts: RungAttempt[] = [];

  const norm = (value: unknown): string =>
    value === null || value === undefined ? "" : String(value).replace(/\s+/g, " ").trim();
  const lower = (value: unknown): string => norm(value).toLowerCase();

  const doc = document;

  const roleOf = (el: Element): string => {
    const explicit = lower(el.getAttribute("role"));
    if (explicit) return explicit.split(" ")[0] ?? "";
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
    if (tag === "A" && !el.hasAttribute("href")) return "generic";
    return tags[tag] ?? "generic";
  };

  const labelTextOf = (el: Element): string => {
    const id = el.getAttribute("id");
    if (id) {
      const escaped = id.replace(/["\\]/g, "\\$&");
      const forLabel = doc.querySelector(`label[for="${escaped}"]`);
      const text = norm(forLabel?.textContent);
      if (text) return text;
    }
    return norm(el.closest("label")?.textContent);
  };

  const accessibleNameOf = (el: Element): string => {
    const labelledby = norm(el.getAttribute("aria-labelledby"));
    if (labelledby) {
      const text = norm(
        labelledby
          .split(" ")
          .map((id) => doc.getElementById(id)?.textContent ?? "")
          .join(" "),
      );
      if (text) return text;
    }
    const ariaLabel = norm(el.getAttribute("aria-label"));
    if (ariaLabel) return ariaLabel;
    const label = labelTextOf(el);
    if (label) return label;
    const tag = el.tagName.toUpperCase();
    if (tag === "INPUT") {
      const type = (el.getAttribute("type") ?? "text").toLowerCase();
      if (type === "submit" || type === "button" || type === "reset") {
        return norm((el as HTMLInputElement).value);
      }
      const placeholder = norm(el.getAttribute("placeholder"));
      if (placeholder) return placeholder;
      return norm(el.getAttribute("title"));
    }
    if (tag === "SELECT" || tag === "TEXTAREA") {
      const placeholder = norm(el.getAttribute("placeholder"));
      if (placeholder) return placeholder;
      return norm(el.getAttribute("title"));
    }
    const alt = norm(el.getAttribute("alt"));
    if (alt) return alt;
    return norm(el.textContent) || norm(el.getAttribute("title"));
  };

  const fuzzyTextOf = (el: Element): string => {
    const tag = el.tagName.toUpperCase();
    if (tag === "INPUT" || tag === "TEXTAREA") {
      return (
        norm(el.getAttribute("placeholder")) ||
        norm(el.getAttribute("aria-label")) ||
        norm((el as HTMLInputElement).value)
      );
    }
    if (tag === "SELECT") {
      return norm(el.getAttribute("placeholder")) || norm((el as HTMLSelectElement).value);
    }
    return norm(el.textContent) || norm(el.getAttribute("title"));
  };

  const isDisabled = (el: Element): boolean => {
    const node = el as HTMLInputElement;
    if (node.disabled === true) return true;
    return lower(el.getAttribute("aria-disabled")) === "true";
  };

  const isVisible = (el: Element): boolean => {
    if (el.hasAttribute("hidden")) return false;
    const view = doc.defaultView;
    const style = view?.getComputedStyle ? view.getComputedStyle(el) : null;
    if (style && (style.display === "none" || style.visibility === "hidden")) return false;
    // An element inside a hidden ancestor also fails the style test above only
    // for itself; walk up for the common `hidden`/display:none case.
    let parent: Element | null = el.parentElement;
    while (parent) {
      if (parent.hasAttribute("hidden")) return false;
      parent = parent.parentElement;
    }
    return true;
  };

  const isUsable = (el: Element): boolean => isVisible(el) && !isDisabled(el);

  const inScope = (el: Element, scope: Element | null): boolean =>
    scope === null || scope.contains(el);

  const elements = (scope: Element | null): Element[] =>
    Array.from((scope ?? doc).querySelectorAll("*"));

  /** Resolve a "kind:Name" container descriptor to the element it names. */
  const container = (descriptor: string): Element | null => {
    const trimmed = norm(descriptor);
    if (!trimmed) return null;
    const colon = trimmed.indexOf(":");
    const kind = lower(colon === -1 ? trimmed : trimmed.slice(0, colon));
    const name = norm(colon === -1 ? "" : trimmed.slice(colon + 1));
    for (const el of elements(null)) {
      const tag = el.tagName.toLowerCase();
      const explicitRole = lower(el.getAttribute("role"));
      if (tag !== kind && explicitRole !== kind && roleOf(el) !== kind) continue;
      if (name && lower(accessibleNameOf(el)) !== lower(name)) continue;
      return el;
    }
    return null;
  };

  const fuzzyScore = (haystack: string, needle: string): number => {
    const text = lower(haystack);
    const target = lower(needle);
    if (!text || !target) return 0;
    if (text === target) return 1;
    if (!text.includes(target)) return 0;
    return target.length / text.length;
  };

  const measure = (el: Element): { x: number; y: number } => {
    try {
      const rect = el.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    } catch {
      return { x: 0, y: 0 };
    }
  };

  const stamp = (el: Element, token: string): void => {
    for (const marked of Array.from(doc.querySelectorAll(`[${MARKER}]`))) {
      marked.removeAttribute(MARKER);
    }
    el.setAttribute(MARKER, token);
  };

  const fail = (reason: string): ResolveResult => ({
    ok: false,
    rung: -1,
    by: "",
    marker: null,
    x: 0,
    y: 0,
    tag: "",
    attempts,
    error: reason,
  });

  const targets = (query.targets ?? []) as ResolveRung[];
  if (targets.length === 0) return fail("the step has no target ladder");

  for (let rung = 0; rung < targets.length; rung += 1) {
    const target = targets[rung] as ResolveRung;
    const by = norm(target.by);
    const scopeDescriptor = norm(target.within) || norm(query.within);
    const scope = scopeDescriptor ? container(scopeDescriptor) : null;

    if (scopeDescriptor && !scope) {
      attempts.push({ rung, by, matches: 0, reason: `container "${scopeDescriptor}" not found` });
      continue;
    }

    let candidates: Element[] = [];

    if (by === "role") {
      const role = lower(target.role);
      const name = norm(target.name);
      candidates = elements(scope).filter(
        (el) =>
          inScope(el, scope) &&
          roleOf(el) === role &&
          (name === "" || lower(accessibleNameOf(el)) === lower(name)),
      );
    } else if (by === "testid") {
      const value = norm(target.value);
      candidates = Array.from(
        (scope ?? doc).querySelectorAll("[data-testid], [data-test]"),
      ).filter((el) => {
        const id = norm(el.getAttribute("data-testid") ?? el.getAttribute("data-test"));
        return id === value;
      });
    } else if (by === "label") {
      const text = lower(target.text);
      const found: Element[] = [];
      for (const label of Array.from((scope ?? doc).querySelectorAll("label"))) {
        if (lower(norm(label.textContent)) !== text) continue;
        const id = label.getAttribute("for");
        const control = id
          ? doc.getElementById(id)
          : (label.querySelector("input, select, textarea") as Element | null);
        if (control) found.push(control);
      }
      candidates = found;
    } else if (by === "css") {
      const selector = norm(target.selector);
      try {
        candidates = Array.from((scope ?? doc).querySelectorAll(selector));
      } catch {
        attempts.push({ rung, by, matches: 0, reason: `invalid selector ${selector}` });
        continue;
      }
    } else if (by === "text_fuzzy") {
      const value = norm(target.value);
      const threshold = typeof target.threshold === "number" ? target.threshold : 0.6;
      const all = elements(scope);
      const matches = all.filter(
        (el) => inScope(el, scope) && fuzzyScore(fuzzyTextOf(el), value) >= threshold,
      );
      // Prefer the deepest match, so a wrapper that merely contains the text is
      // not chosen over the element that actually carries it.
      candidates = matches.filter(
        (el) => !matches.some((other) => other !== el && el.contains(other)),
      );
    } else {
      attempts.push({ rung, by, matches: 0, reason: `unknown rung ${by}` });
      continue;
    }

    const usable = candidates.filter(isUsable);
    if (usable.length === 1) {
      const element = usable[0] as Element;
      const box = measure(element);
      stamp(element, query.token);
      attempts.push({ rung, by, matches: 1 });
      return {
        ok: true,
        rung,
        by,
        marker: query.token,
        x: box.x,
        y: box.y,
        tag: element.tagName.toLowerCase(),
        attempts,
      };
    }

    attempts.push({
      rung,
      by,
      matches: usable.length,
      reason: usable.length === 0 ? "no visible, enabled match" : "more than one match",
    });
  }

  return fail("no rung of the target ladder resolved to exactly one element");
}

/** The resolver as a source string, for injection into a page context. */
export function resolverExpression(): string {
  return `(${resolveTargetInPage.toString()})`;
}

/** An ES-module-safe name for the resolver, used in tests. */
export const resolveTargetSource = resolverExpression;
