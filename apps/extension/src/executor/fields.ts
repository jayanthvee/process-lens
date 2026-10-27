// PL-114: fields a person clicks rather than types, set to the row's value.
//
// `fieldActionInPage` runs inside the page, the way `resolveTargetInPage` and
// `measureTargetInPage` do, so it is ONE self-contained function: the CDP call serializes it with
// `toString()`, and anything it needed from this module would not exist on the other side. It
// finds the element the resolver marked with `data-pl-target` and either sets it, chooses in it,
// or reads it back.
//
// Every mode is idempotent: setting a field to the state it already has changes nothing and
// reports success, so a replay never flips a checkbox that is already ticked.
//
// The rules are general: a control is recognised by its HTML type or its ARIA role, never by a
// site's name, class or label.

export type FieldMode = "check" | "choose" | "number" | "read";

export interface FieldQuery {
  mode: FieldMode;
  /** The token the resolver stamped on the element. */
  token: string;
  /** check: the state to leave it in. choose: the option's label. number: the number. */
  value?: boolean | number | string;
}

export interface FieldResult {
  ok: boolean;
  /** check/number: whether the page actually changed. */
  changed?: boolean;
  /** check: the state the field is in now. */
  state?: boolean;
  /** choose: whether an option matched. */
  matched?: boolean;
  /** choose: every option the group offers, so a miss can name them. */
  options?: string[];
  /** number: what the control shows now. */
  shown?: number | null;
  /** read: what the field shows now. */
  text?: string;
  reason?: string;
}

export function fieldActionInPage(query: FieldQuery): FieldResult {
  const MARKER = "data-pl-target";

  const marked = (): { el: Element | null; reason?: string } => {
    let selector: string;
    try {
      selector = `[${MARKER}="${String(query.token).replace(/["\\]/g, "\\$&")}"]`;
    } catch {
      return { el: null, reason: "the marker token is not a valid selector" };
    }
    const light = document.querySelector(selector);
    if (light) return { el: light };
    const collect = (root: Document | ShadowRoot): Element | null => {
      for (const host of Array.from(root.querySelectorAll("*"))) {
        const shadow = host.shadowRoot;
        if (!shadow) continue;
        const found = shadow.querySelector(selector) ?? collect(shadow);
        if (found) return found;
      }
      return null;
    };
    const found = collect(document);
    return found
      ? { el: found }
      : { el: null, reason: "the marked element is no longer in the document" };
  };

  const nameOf = (el: Element): string => {
    const aria = el.getAttribute("aria-label");
    if (aria && aria.trim()) return aria.replace(/\s+/g, " ").trim();
    const id = el.getAttribute("id");
    if (id) {
      const label = document.querySelector(`label[for="${id.replace(/"/g, '\\"')}"]`);
      if (label?.textContent?.trim()) return label.textContent.replace(/\s+/g, " ").trim();
    }
    const wrapping = el.closest("label");
    if (wrapping?.textContent?.trim()) return wrapping.textContent.replace(/\s+/g, " ").trim();
    const text = (el.textContent ?? "").replace(/\s+/g, " ").trim();
    if (text) return text;
    const value = el.getAttribute("value");
    return value ? value.replace(/\s+/g, " ").trim() : "";
  };

  const disabled = (el: Element): boolean =>
    (el as HTMLInputElement).disabled === true ||
    el.getAttribute("aria-disabled") === "true" ||
    el.hasAttribute("disabled");

  const visible = (el: Element): boolean => {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style =
      typeof window.getComputedStyle === "function" ? window.getComputedStyle(el) : null;
    return style === null || (style.visibility !== "hidden" && style.display !== "none");
  };

  const clickIt = (el: Element): void => {
    (el as HTMLElement).click?.();
  };

  const found = marked();
  const el = found.el;
  if (!el) return { ok: false, reason: found.reason };

  if (query.mode === "read") {
    const tag = el.tagName.toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") {
      return { ok: true, text: (el as HTMLInputElement).value };
    }
    const now = el.getAttribute("aria-valuenow");
    if (now !== null && now !== "") return { ok: true, text: now };
    return { ok: true, text: (el.textContent ?? "").replace(/\s+/g, " ").trim() };
  }

  if (query.mode === "check") {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") ?? "").toLowerCase();
    const role = (el.getAttribute("role") ?? "").toLowerCase();
    const toggle =
      (tag === "input" && type === "checkbox") || role === "checkbox" || role === "switch";
    if (!toggle) {
      return { ok: false, reason: "the field is not a checkbox or switch" };
    }
    const stateOf = (): boolean => {
      if (tag === "input") return (el as HTMLInputElement).checked === true;
      return (
        el.getAttribute("aria-checked") === "true" || el.getAttribute("aria-pressed") === "true"
      );
    };
    const wanted = query.value === true;
    if (disabled(el)) return { ok: false, state: stateOf(), reason: "the field is disabled" };
    const already = stateOf();
    if (already === wanted) return { ok: true, changed: false, state: already };
    clickIt(el);
    const after = stateOf();
    if (after !== wanted) {
      return {
        ok: false,
        changed: after !== already,
        state: after,
        reason: "the field did not take the value",
      };
    }
    return { ok: true, changed: true, state: after };
  }

  if (query.mode === "choose") {
    const wanted = String(query.value ?? "");
    // A rating row is one kind of option repeated, whose names do not distinguish the options:
    // all identical (five stars that all say "★"), all empty, or each carrying its own 1-based
    // place ("1 of 5", "2 of 5", ...). Anything else -- Goods, Service, Combo -- is a named group,
    // and a number there is a value it does not offer.
    const isRatingRow = (options: Element[], labels: string[]): boolean => {
      if (options.length < 2) return false;
      const kindOf = (option: Element): string =>
        `${option.tagName.toLowerCase()}|${(option.getAttribute("role") ?? "").toLowerCase()}`;
      const kind = kindOf(options[0] as Element);
      if (!options.every((option) => kindOf(option) === kind)) return false;
      const uniform = labels.every((label) => label === labels[0]);
      if (uniform) return true;
      const shapes = labels.map((label) => label.replace(/\d+/g, "#"));
      if (!shapes.every((shape) => shape === shapes[0])) return false;
      return labels.every((label, position) => {
        const first = /\d+/.exec(label);
        return first !== null && Number(first[0]) === position + 1;
      });
    };
    const selector = [
      "input[type='radio']",
      "[role='radio']",
      "[role='option']",
      "button[aria-checked]",
      "[aria-current]",
      "[role='tab']",
    ].join(",");
    const inside = Array.from(el.querySelectorAll(selector));
    // A custom combobox is not a group: it has no options until it is opened. The caller opens it
    // and clicks the option instead, so this mode says so rather than inventing a miss.
    const ownRole = (el.getAttribute("role") ?? "").toLowerCase();
    const ownType = (el.getAttribute("type") ?? "").toLowerCase();
    const isOptionItself =
      ownType === "radio" ||
      ["radio", "option", "tab"].includes(ownRole) ||
      el.hasAttribute("aria-checked") ||
      el.hasAttribute("aria-current");
    if (inside.length === 0 && !isOptionItself) {
      return { ok: false, matched: false, options: [], reason: "not a group" };
    }
    const options = inside.length > 0 ? inside : [el];
    const shown = options.filter((option) => visible(option) || option === el);
    const names = shown.map(nameOf);
    const normalise = (text: string): string => text.replace(/\s+/g, " ").trim().toLowerCase();
    let index = names.findIndex((name) => name === wanted);
    if (index < 0) index = names.findIndex((name) => normalise(name) === normalise(wanted));
    // PL-114 item 5, narrowed by T1 review B2: a rating row's options carry no label of their own,
    // so there the row's number is the option's place: 3 is the third star. Anywhere else a bare
    // number is a value the group does not offer, and guessing by position in a batch loop is
    // exactly what the review forbids -- "2" against Goods/Service/Combo must park, not pick
    // Service. So the row first has to look like a rating row: one kind of option, and names that
    // do not tell the options apart (all the same, or each one's own place written into it).
    if (index < 0 && /^\d+$/.test(wanted.trim()) && isRatingRow(shown, names)) {
      const ordinal = Number(wanted.trim());
      if (ordinal >= 1 && ordinal <= shown.length) index = ordinal - 1;
    }
    if (index < 0) {
      return {
        ok: false,
        matched: false,
        options: names.filter((name) => name !== ""),
        reason: "no option matched",
      };
    }
    const option = shown[index];
    if (option === undefined) {
      return { ok: false, matched: false, options: names, reason: "no option matched" };
    }
    const chosen = (): boolean => {
      if (option.tagName.toLowerCase() === "input") {
        return (option as HTMLInputElement).checked === true;
      }
      return (
        option.getAttribute("aria-checked") === "true" ||
        option.getAttribute("aria-current") === "true" ||
        option.getAttribute("aria-selected") === "true"
      );
    };
    if (disabled(option)) {
      return { ok: false, matched: true, options: names, reason: "the option is disabled" };
    }
    if (chosen()) return { ok: true, matched: true, options: names };
    clickIt(option);
    // A control that does not report its own state is still chosen by the click; the step's own
    // check is what proves the page kept it.
    return { ok: true, matched: true, options: names };
  }

  // number
  const wanted = Number(query.value);
  if (!Number.isFinite(wanted)) {
    return { ok: false, shown: null, reason: "the value is not a number" };
  }
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute("type") ?? "").toLowerCase();
  const role = (el.getAttribute("role") ?? "").toLowerCase();

  const numeric = (node: Element): number | null => {
    if (node.tagName.toLowerCase() === "input") {
      const raw = (node as HTMLInputElement).value;
      const parsed = Number(raw);
      return raw !== "" && Number.isFinite(parsed) ? parsed : null;
    }
    const now = node.getAttribute("aria-valuenow");
    if (now !== null && now !== "" && Number.isFinite(Number(now))) return Number(now);
    const text = (node.textContent ?? "").replace(/[^0-9.-]/g, "");
    return text !== "" && Number.isFinite(Number(text)) ? Number(text) : null;
  };

  const write = (node: Element, value: number): void => {
    if (node.tagName.toLowerCase() === "input") {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      if (setter) setter.call(node as HTMLInputElement, String(value));
      else (node as HTMLInputElement).value = String(value);
    } else {
      node.setAttribute("aria-valuenow", String(value));
    }
    node.dispatchEvent(new Event("input", { bubbles: true }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
  };

  if (tag === "input" && (type === "range" || type === "number")) {
    if (disabled(el)) return { ok: false, shown: numeric(el), reason: "the field is disabled" };
    const before = numeric(el);
    if (before === wanted) return { ok: true, changed: false, shown: before };
    write(el, wanted);
    const after = numeric(el);
    return after === wanted
      ? { ok: true, changed: true, shown: after }
      : {
          ok: false,
          changed: after !== before,
          shown: after,
          reason: "the field did not take the value",
        };
  }

  if (role === "slider") {
    const before = numeric(el);
    if (before === wanted) return { ok: true, changed: false, shown: before };
    write(el, wanted);
    const after = numeric(el);
    return after === wanted
      ? { ok: true, changed: true, shown: after }
      : {
          ok: false,
          changed: after !== before,
          shown: after,
          reason: "the field did not take the value",
        };
  }

  // A plain text field is typed, not set: say so, and let the caller's own fill path run.
  if (tag === "input") {
    return { ok: false, shown: numeric(el), reason: "not a number control" };
  }

  // A stepper: the marked element is the number, and its +/- buttons sit beside it.
  const container = el.closest("form, [role='group'], fieldset") ?? el.parentElement ?? el;
  const buttons = Array.from(container.querySelectorAll("button, [role='button']"));
  const stepButton = (sign: 1 | -1): Element | null => {
    for (const button of buttons) {
      const words = nameOf(button).replace(/\s+/g, " ").trim().toLowerCase();
      if (sign > 0 && /^(\+|plus|increase|increment|add one|one more)$/.test(words)) return button;
      if (sign < 0 && /^(-|minus|decrease|decrement|remove one|one less)$/.test(words)) return button;
    }
    return null;
  };
  let current = numeric(el);
  if (current === null) return { ok: false, shown: null, reason: "the field shows no number" };
  for (let press = 0; press < 100 && current !== wanted; press += 1) {
    const button = stepButton(wanted > current ? 1 : -1);
    if (!button) {
      return {
        ok: false,
        changed: press > 0,
        shown: current,
        reason: "the control has no +/- button",
      };
    }
    clickIt(button);
    const next = numeric(el);
    if (next === null || next === current) {
      return {
        ok: false,
        changed: press > 0,
        shown: current,
        reason: "the control stopped at a limit",
      };
    }
    current = next;
  }
  if (current !== wanted) {
    return { ok: false, changed: true, shown: current, reason: "the control stopped before the value" };
  }
  return { ok: true, changed: true, shown: current };
}

/** The page-side function as an injectable expression, like the resolver's own. */
export function fieldActionSource(): string {
  return `(${fieldActionInPage.toString()})`;
}
