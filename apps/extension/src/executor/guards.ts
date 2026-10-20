// The guards a command's input must pass before it is dispatched (PL-014).
//
// Every guard here is fail-closed: when it cannot prove the input is safe, it
// refuses and names an event the runner turns into a pause. None of them
// solves, skips, or bypasses a challenge or a login — those are a person's job.
import type { EventKind } from "../shared/command-protocol";
import type { CdpTransport } from "./cdp";
import { evaluate } from "./page";
import type { ExecutorStep } from "./types";

/** Everything a guard needs to judge one step. */
export interface GuardContext {
  cdp: CdpTransport;
  contextId?: number;
  step: ExecutorStep;
  allowedOrigins: string[] | undefined;
}

/** The result of a guard: a pass, or a refusal with an optional event kind. */
export type GuardResult = { ok: true } | { ok: false; event?: EventKind; reason: string };

/** The readable sentence a policy-blocked attach becomes. */
export const POLICY_BLOCKED_MESSAGE = "your administrator's Chrome policy blocks ProcessLens";

/**
 * The destructive verbs a control's accessible name must not begin with. The
 * match is a *word prefix* on purpose: `\bpay\b` would let "Payment" through,
 * and a "Payment" button must be refused like any other money-moving control.
 */
const DESTRUCTIVE_PREFIX = /\b(delete|cancel|refund|pay|transfer)/i;

/** The matched prefix of a destructive verb in an accessible name, or null. */
export function destructiveMatch(accessibleName: string): string | null {
  const matched = DESTRUCTIVE_PREFIX.exec(accessibleName ?? "");
  if (!matched) return null;
  return (matched[1] ?? "").toLowerCase();
}

function evaluateOptions(ctx: GuardContext): { contextId?: number } {
  return ctx.contextId !== undefined ? { contextId: ctx.contextId } : {};
}

/**
 * Require the target frame's own origin to be in `allowed_origins` (when the
 * list is non-empty). The origin is read in the frame's own context, so a
 * navigate a step performed cannot be mistaken for the frame it is acting on.
 */
export async function checkOrigin(ctx: GuardContext): Promise<GuardResult> {
  const allowed = ctx.allowedOrigins ?? [];
  if (allowed.length === 0) return { ok: true };
  const origin = (await evaluate(ctx.cdp, "location.origin", evaluateOptions(ctx))) as unknown;
  const value = typeof origin === "string" ? origin : "";
  if (!allowed.includes(value)) {
    return {
      ok: false,
      event: "off_origin",
      reason: `the page origin ${value || "(unknown)"} is not in allowed_origins`,
    };
  }
  return { ok: true };
}

/** The shape the page-side challenge/login probe answers with. */
export interface ChallengeProbe {
  challenge: boolean;
  marker: string;
  password_field: boolean;
  password_name: string;
  /** PL-068: the page is asking for a verification code, and its own heading. */
  code_field: boolean;
  code_heading: string;
}

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
  if (singles.length >= 4 && singles.length <= 8) {
    const sameRow = singles.every((input) => {
      const box = input.getBoundingClientRect();
      const first = singles[0].getBoundingClientRect();
      return Math.abs(box.top - first.top) < 40;
    });
    if (sameRow) {
      return { found: true, heading: headingText(singles[0]), split: true };
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

/**
 * One page expression that reports whether the frame is showing a challenge or
 * an unrelated login form. Runs in the page: the guard must judge what is
 * actually rendered, not what the command expected.
 *
 * A challenge is a challenge iframe (`challenges.cloudflare.com`, `recaptcha`,
 * `hcaptcha`, `turnstile`) or a document title containing "Just a moment". A
 * login prompt is a *visible* password field that is not this step's own target
 * — a fill into the step's own field is not a login.
 */
export function challengeOrLoginInPage(query: { targets: unknown[] }): ChallengeProbe {
  const targets = (query.targets ?? []) as Array<Record<string, unknown>>;
  const marked = document.querySelector("[data-pl-target]");

  const isVisible = (el: Element): boolean => {
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const isStepTarget = (el: Element): boolean => {
    if (marked && (marked === el || marked.contains(el) || el.contains(marked))) return true;
    return targets.some((target) => {
      const by = String(target["by"] ?? "");
      const value = target["value"];
      if (by === "testid" && typeof value === "string") {
        const id = el.getAttribute("data-testid") ?? el.getAttribute("data-test");
        return id === value;
      }
      if (by === "css" && typeof target["selector"] === "string") {
        try {
          return el.matches(target["selector"] as string);
        } catch {
          return false;
        }
      }
      return false;
    });
  };

  const challengeSrc = /challenges\.cloudflare\.com|recaptcha|hcaptcha|turnstile/i;
  let marker = "";
  for (const frame of Array.from(document.querySelectorAll("iframe"))) {
    const src = frame.getAttribute("src") ?? "";
    if (!challengeSrc.test(src)) continue;
    // Only a *visible* challenge iframe counts (Requirement 8). An ordinary form usually embeds a
    // zero-size reCAPTCHA badge to obtain a token; its src matches the challenge pattern but
    // nothing is being asked of the person, so pausing the run for it would park records that
    // need no intervention. A challenge that is really shown occupies a box.
    if (!isVisible(frame)) continue;
    marker = src || "challenge iframe";
    break;
  }
  if (!marker && /just a moment/i.test(document.title ?? "")) marker = "Just a moment";

  let passwordName = "";
  for (const input of Array.from(document.querySelectorAll('input[type="password"]'))) {
    if (!isVisible(input)) continue;
    if (isStepTarget(input)) continue;
    passwordName = input.getAttribute("name") ?? input.getAttribute("id") ?? "password";
    break;
  }

  // PL-068: no challenge and no password, but the page is asking for a code.
  const code =
    marker === "" && passwordName === ""
      ? (codePromptInPage as (q: { targets: unknown[] }) => CodePromptProbe)({ targets })
      : { found: false, heading: "", split: false };

  return {
    challenge: marker !== "",
    marker,
    password_field: passwordName !== "",
    password_name: passwordName,
    code_field: code.found,
    code_heading: code.heading,
  };
}

/**
 * Refuse a step whose frame is showing a challenge or a login the step is not
 * itself filling. Never solves or bypasses either: a challenge is a person's to
 * clear, and a login the recipe did not describe is a wrong-page signal.
 */
export async function checkChallengeOrLogin(ctx: GuardContext): Promise<GuardResult> {
  // The probe runs in the page, so every function it calls must travel with it: a module
  // reference would not exist inside `new Function(...)`.
  // The probe runs in the page, so every function it calls must travel with it: a module
  // reference would not exist inside the evaluated expression. The page-side evaluator takes
  // one expression, so the helper is bound inside an arrow that is called at once.
  const expression =
    `(() => { const codePromptInPage = ${codePromptInPage.toString()};` +
    ` return (${challengeOrLoginInPage.toString()})(${JSON.stringify({
      targets: ctx.step.targets ?? [],
    })}) })()`;
  const probe = (await evaluate(ctx.cdp, expression, evaluateOptions(ctx))) as
    | ChallengeProbe
    | undefined;
  if (probe?.challenge) {
    return {
      ok: false,
      event: "challenge",
      reason: `the page is showing a challenge (${probe.marker}); ProcessLens never solves or bypasses one`,
    };
  }
  if (probe?.password_field) {
    return {
      ok: false,
      event: "login_required",
      reason: `the page is asking for a password (${probe.password_name}), which this step is not; sign in first`,
    };
  }
  if (probe?.code_field) {
    // PL-068: a person supplies the code on the run page; ProcessLens never reads an inbox or SMS.
    return {
      ok: false,
      event: "code_required",
      reason: probe.code_heading || "the site is asking for a verification code",
    };
  }
  return { ok: true };
}

/** Whether the step explicitly opted in to moving money or deleting data. */
function allowsDestructive(step: ExecutorStep): boolean {
  return (step as { allow_destructive_control?: boolean }).allow_destructive_control === true;
}

/** The name a step's own ladder declares, used when no resolved name is given. */
function declaredName(step: ExecutorStep): string {
  for (const target of step.targets ?? []) {
    const record = target as { name?: unknown; value?: unknown; text?: unknown };
    if (typeof record.name === "string" && record.name) return record.name;
    if (typeof record.value === "string" && record.value) return record.value;
    if (typeof record.text === "string" && record.text) return record.text;
  }
  return "";
}

/**
 * Refuse a click on a control whose name looks destructive unless the step
 * carries `allow_destructive_control`. Only a click can commit a deletion, a
 * refund, or a payment, so a fill or a select passes.
 *
 * The name to judge is the *accessible name of the element the ladder matched* — what a person
 * reading the page sees — which the caller passes once it has resolved the target. The step's own
 * declared name is the fallback, for a caller that has not resolved one yet.
 */
export function checkDestructive(ctx: GuardContext, accessibleName?: string): GuardResult {
  if (ctx.step.action !== "click") return { ok: true };
  if (allowsDestructive(ctx.step)) return { ok: true };
  const declared = ctx.step.name ?? declaredName(ctx.step);
  const name = accessibleName || declared;
  const matched = destructiveMatch(name);
  if (!matched) return { ok: true };
  return {
    ok: false,
    reason: `the control ${JSON.stringify(name || ctx.step.id)} looks destructive ("${matched}"); set allow_destructive_control on the step to permit it`,
  };
}

/**
 * Classify a debugger attach failure. Chrome refuses to attach (or to capture a
 * screenshot) when an administrator's policy forbids it; that is not a
 * transient error and must be surfaced as a readable sentence, not retried.
 * Anything else keeps today's behaviour (null).
 */
export function classifyAttachError(message: string): "policy_blocked" | null {
  if (/policy/i.test(message ?? "") && /restrict/i.test(message ?? "")) return "policy_blocked";
  return null;
}

/**
 * Re-check a command's expiry immediately before its first input event (R4). A
 * command is valid for the moment it was received AND for the moment its input
 * is sent; a slow prepare must not let a stale command type into a page. It is
 * synchronous and cheap so it can sit on the input path itself, one call before
 * every trusted input event (F1).
 */
export function recheckExpiry(expiresAt: number, now: () => number): GuardResult {
  if (now() >= expiresAt) {
    return { ok: false, reason: "the command expired before any input was sent" };
  }
  return { ok: true };
}
