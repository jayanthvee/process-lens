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
  /** The warning-page phrase that matched, when the hit came from PL-067's markers. */
  warning_marker: string;
  password_field: boolean;
  password_name: string;
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

  // PL-067: rate-limit and unusual-activity pages, on page-level surfaces only -- the title, a
  // visible heading, or a dialog's text. The list lives here, inside the function, because this
  // function is serialized into the page and cannot see the module's scope. Each marker matches
  // ONE surface; the page's own copy (a LinkedIn job description that says "security check") is
  // never a marker, and an input's value is never read. Every hit raises the existing `challenge`
  // event: the run pauses and nothing is clicked or solved. A rate limit, not a disguise.
  const WARNING_PAGE_MARKERS: ReadonlyArray<{ phrase: string; where: "text" | "title" }> = [
    { phrase: "unusual activity", where: "text" },
    { phrase: "we've restricted", where: "text" },
    { phrase: "too many requests", where: "text" },
    { phrase: "verify you're human", where: "text" },
    { phrase: "temporarily blocked", where: "text" },
    { phrase: "security check", where: "text" },
    { phrase: "Security Verification", where: "title" },
  ];
  let warningMarker = "";
  const surfaces = [
    document.title ?? "",
    ...Array.from(document.querySelectorAll("h1, h2, h3, h4, h5, h6"))
      .filter(isVisible)
      .map((heading) => heading.textContent ?? ""),
    ...Array.from(document.querySelectorAll("dialog, [role='dialog']"))
      .filter(isVisible)
      .map((dialog) => dialog.textContent ?? ""),
  ];
  const pageText = surfaces.join("\n").toLowerCase();
  for (const markerSpec of WARNING_PAGE_MARKERS) {
    const phrase = markerSpec.phrase.toLowerCase();
    const hit =
      markerSpec.where === "title"
        ? (document.title ?? "").toLowerCase().includes(phrase)
        : pageText.includes(phrase);
    if (hit) {
      warningMarker = markerSpec.phrase;
      break;
    }
  }

  let passwordName = "";
  for (const input of Array.from(document.querySelectorAll('input[type="password"]'))) {
    if (!isVisible(input)) continue;
    if (isStepTarget(input)) continue;
    passwordName = input.getAttribute("name") ?? input.getAttribute("id") ?? "password";
    break;
  }

  return {
    challenge: marker !== "" || warningMarker !== "",
    marker,
    warning_marker: warningMarker,
    password_field: passwordName !== "",
    password_name: passwordName,
  };
}

/**
 * Refuse a step whose frame is showing a challenge or a login the step is not
 * itself filling. Never solves or bypasses either: a challenge is a person's to
 * clear, and a login the recipe did not describe is a wrong-page signal.
 */
export async function checkChallengeOrLogin(ctx: GuardContext): Promise<GuardResult> {
  const expression = `(${challengeOrLoginInPage.toString()})(${JSON.stringify({
    targets: ctx.step.targets ?? [],
  })})`;
  const probe = (await evaluate(ctx.cdp, expression, evaluateOptions(ctx))) as
    | ChallengeProbe
    | undefined;
  if (probe?.challenge) {
    // PL-067: a warning page names itself in the sentence, so the person knows which page to
    // look at. ProcessLens never pushes past it either way.
    const reason = probe.warning_marker
      ? `The site showed a warning page ('${probe.warning_marker}'). Check it yourself, then Resume.`
      : `the page is showing a challenge (${probe.marker}); ProcessLens never solves or bypasses one`;
    return {
      ok: false,
      event: "challenge",
      reason,
    };
  }
  if (probe?.password_field) {
    return {
      ok: false,
      event: "login_required",
      reason: `the page is asking for a password (${probe.password_name}), which this step is not; sign in first`,
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
