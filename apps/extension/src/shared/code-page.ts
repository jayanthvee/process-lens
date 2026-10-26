// PL-068/PL-107: the page-side rules for a one-time verification code.
//
// Both functions run inside the page: codePromptInPage decides whether the page is asking for
// a code, and enterCodeInPage types the code and presses the page's own continue. They are
// kept here, apart from the guards that serialize them with .toString(), so the recorder can
// call them directly while teaching without pulling the executor's CDP code into its bundle.
//
// Neither function returns, stores or logs the code.

/** The shape the page-side verification-code probe answers with. */
export interface CodePromptProbe {
  found: boolean;
  heading: string;
  split: boolean;
}

/**
 * Whether this page asks for a one-time code, and what it calls the request (PL-068).
 *
 * Run in the page, like `challengeOrLoginInPage`. A code page is a *visible* text field that says
 * it wants a code, by any of three durable signals:
 *
 * * `autocomplete="one-time-code"` -- the standard hint, and the strongest;
 * * a numeric field (`inputmode="numeric"`) of 4-8 characters whose label, placeholder or nearby
 *   heading says code / verification / one-time;
 * * 4-8 single-character boxes in a row -- the split layout most banks and sign-in flows use.
 *
 * A password field means a login page, not a code page: the run must pause `login_required` there,
 * and ProcessLens never types a password. A "Promo code" field is not a verification code: it
 * carries neither the one-time-code hint nor the verification wording.
 *
 * The heading is the page's own: the nearest heading, else the field's label or placeholder, else
 * the document title. Never the URL, whose query string may carry a code or a token.
 */
export function codePromptInPage(_query: { targets?: unknown[] } = {}): CodePromptProbe {
  const isVisible = (el: Element): boolean => {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(el);
    return style.visibility !== "hidden" && style.display !== "none";
  };
  const isEnabled = (el: Element): boolean => !(el as HTMLInputElement).disabled;
  const verification = /(verification|one[- ]time|security code|\bcode\b)/i;

  // A password field makes this a login page whatever else is on it.
  for (const input of Array.from(document.querySelectorAll('input[type="password"]'))) {
    if (isVisible(input) && isEnabled(input)) {
      return { found: false, heading: "", split: false };
    }
  }

  const inputs = Array.from(document.querySelectorAll("input")).filter(
    (input) =>
      isVisible(input) &&
      isEnabled(input) &&
      ["text", "tel", "number", "search", ""].includes(input.getAttribute("type") ?? ""),
  );
  if (inputs.length === 0) return { found: false, heading: "", split: false };

  const labelFor = (input: Element): string => {
    const id = input.getAttribute("id");
    const explicit = id ? document.querySelector(`label[for="${id}"]`) : null;
    const wrapping = input.closest("label");
    const parts = [
      explicit?.textContent ?? "",
      wrapping?.textContent ?? "",
      input.getAttribute("aria-label") ?? "",
      input.getAttribute("placeholder") ?? "",
      input.getAttribute("name") ?? "",
    ];
    return parts.join(" ").replace(/\s+/g, " ").trim();
  };
  const headingText = (input: Element): string => {
    let node: Element | null = input.closest("form") ?? input.parentElement;
    for (let depth = 0; depth < 4 && node; depth += 1) {
      const heading = node.querySelector("h1, h2, h3, [role='heading']");
      const text = (heading?.textContent ?? "").replace(/\s+/g, " ").trim();
      if (text) return text;
      node = node.parentElement;
    }
    return (document.title ?? "").trim();
  };

  // 1. The standard hint.
  for (const input of inputs) {
    const hint = (input.getAttribute("autocomplete") ?? "").toLowerCase();
    if (hint.includes("one-time-code")) {
      return { found: true, heading: headingText(input) || labelFor(input), split: false };
    }
  }

  // 2. A numeric field of code length whose own words say what it wants.
  for (const input of inputs) {
    const inputmode = (input.getAttribute("inputmode") ?? "").toLowerCase();
    const maxLength = Number(input.getAttribute("maxlength") ?? "0");
    if (inputmode !== "numeric") continue;
    if (!(maxLength >= 4 && maxLength <= 8)) continue;
    const words = `${labelFor(input)} ${headingText(input)}`;
    if (verification.test(words)) {
      return { found: true, heading: headingText(input) || labelFor(input), split: false };
    }
  }

  // 3. 4-8 single-character boxes side by side.
  const singles = inputs.filter((input) => input.getAttribute("maxlength") === "1");
  const firstSingle = singles[0];
  if (firstSingle && singles.length >= 4 && singles.length <= 8) {
    const first = firstSingle.getBoundingClientRect();
    const sameRow = singles.every(
      (input) => Math.abs(input.getBoundingClientRect().top - first.top) < 40,
    );
    if (sameRow) {
      return { found: true, heading: headingText(firstSingle), split: true };
    }
  }

  return { found: false, heading: "", split: false };
}

/** The shape the page-side code entry answers with. It never carries the code. */
export interface CodeEntryResult {
  entered: boolean;
  reason: string;
}

/**
 * Type a person's code into the page's code field(s) and press the page's own continue (PL-068).
 *
 * Runs in the page, exactly like `codePromptInPage`. Split boxes get one character each; a single
 * field gets the whole code. The submit is the page's single primary control -- a
 * `button[type=submit]`, or the only enabled in-form button whose words are Verify / Continue /
 * Submit / Next -- and when there is no such control exactly once, Enter in the last box.
 *
 * The answer says `entered` or `not_found` and never echoes, stores or returns the code.
 */
export function enterCodeInPage(input: { code: string }): CodeEntryResult {
  const code = String(input.code ?? "");
  const probe = (codePromptInPage as (q: { targets?: unknown[] }) => CodePromptProbe)({});
  if (!probe.found) return { entered: false, reason: "not_found" };

  const isVisible = (el: Element): boolean => {
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const all = Array.from(document.querySelectorAll("input")).filter(
    (el) => isVisible(el) && !(el as HTMLInputElement).disabled,
  );
  const fields = probe.split
    ? all.filter((el) => el.getAttribute("maxlength") === "1")
    : all.filter((el) => {
        const hint = (el.getAttribute("autocomplete") ?? "").toLowerCase();
        return hint.includes("one-time-code") || el.getAttribute("inputmode") === "numeric";
      });
  if (fields.length === 0) return { entered: false, reason: "not_found" };

  const setValue = (field: HTMLInputElement, value: string): void => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    if (setter) setter.call(field, value);
    else field.value = value;
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
  };

  if (probe.split) {
    fields.forEach((field, index) => {
      const character = code[index] ?? "";
      (field as HTMLInputElement).focus?.();
      setValue(field as HTMLInputElement, character);
    });
  } else {
    const field = fields[0] as HTMLInputElement;
    field.focus?.();
    setValue(field, code);
  }

  const last = fields[fields.length - 1] as HTMLInputElement;
  const form = last.closest("form");
  const words = /^(verify|continue|submit|next)$/i;
  const buttons = Array.from((form ?? document).querySelectorAll("button")).filter(
    (button) => isVisible(button) && !(button as HTMLButtonElement).disabled,
  );
  const primary = buttons.filter(
    (button) =>
      button.getAttribute("type") === "submit" ||
      words.test((button.textContent ?? "").replace(/\s+/g, " ").trim()),
  );
  if (primary.length === 1) {
    (primary[0] as HTMLButtonElement).click();
    return { entered: true, reason: "submitted" };
  }

  for (const type of ["keydown", "keypress", "keyup"] as const) {
    last.dispatchEvent(new KeyboardEvent(type, { key: "Enter", bubbles: true }));
  }
  if (form && typeof form.requestSubmit === "function") form.requestSubmit();
  return { entered: true, reason: "pressed Enter" };
}
