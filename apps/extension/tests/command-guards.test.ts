// The pre-dispatch guards (PL-014): origin, challenge/login, destructive, the
// attach-error classifier, and the pre-input expiry re-check. The page-side
// probes run against jsdom through the shared fake transport, exactly as the
// real guard does in a tab.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  POLICY_BLOCKED_MESSAGE,
  checkChallengeOrLogin,
  checkDestructive,
  checkOrigin,
  classifyAttachError,
  destructiveMatch,
  recheckExpiry,
} from "../src/executor/guards";
import type { ExecutorStep } from "../src/executor/types";
import { fakeCdp } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

beforeEach(() => {
  document.body.innerHTML = "";
  document.title = "Test page";
  restoreLayout = installLayout();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

function step(overrides: Partial<ExecutorStep> = {}): ExecutorStep {
  return { id: "step-1", action: "click", ...overrides };
}

describe("checkOrigin", () => {
  it("refuses a frame whose origin is not in allowed_origins", async () => {
    const { cdp } = fakeCdp();
    const result = await checkOrigin({
      cdp,
      step: step(),
      allowedOrigins: ["https://app.example.com"],
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.event).toBe("off_origin");
      expect(result.reason).toMatch(/allowed_origins/);
    }
  });

  it("passes when the frame's own origin is allowed", async () => {
    const { cdp } = fakeCdp();
    const result = await checkOrigin({
      cdp,
      step: step(),
      allowedOrigins: [window.location.origin],
    });
    expect(result.ok).toBe(true);
  });

  it("passes when no origins are declared", async () => {
    const { cdp } = fakeCdp();
    expect((await checkOrigin({ cdp, step: step(), allowedOrigins: undefined })).ok).toBe(true);
  });
});

describe("checkChallengeOrLogin", () => {
  it("a challenge marker is detected and the command is refused", async () => {
    document.body.innerHTML =
      "<iframe src='https://challenges.cloudflare.com/turnstile/v0/api.js'></iframe>";
    const { cdp } = fakeCdp();
    const result = await checkChallengeOrLogin({ cdp, step: step(), allowedOrigins: [] });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.event).toBe("challenge");
      expect(result.reason).toMatch(/challenge/i);
    }
  });

  it("a challenge title is detected too", async () => {
    document.title = "Just a moment...";
    const { cdp } = fakeCdp();
    const result = await checkChallengeOrLogin({ cdp, step: step(), allowedOrigins: [] });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.event).toBe("challenge");
  });

  it("a visible challenge iframe is a challenge", async () => {
    document.body.innerHTML =
      "<iframe src='https://challenges.cloudflare.com/turnstile/v0/api.js'></iframe>";
    const frame = document.querySelector("iframe") as HTMLIFrameElement;
    frame.getBoundingClientRect = () => ({ width: 300, height: 65 }) as DOMRect;
    const { cdp } = fakeCdp();
    const result = await checkChallengeOrLogin({ cdp, step: step(), allowedOrigins: [] });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.event).toBe("challenge");
  });

  it("an invisible reCAPTCHA badge does not pause the run", async () => {
    // Requirement 8: a normal form embeds the reCAPTCHA badge at a zero box purely to get a token.
    // jsdom has no layout engine and the layout stub hands every element a non-zero rect, so the
    // badge's real styling (width:0;height:0, display:none) is reproduced on the element itself.
    document.body.innerHTML =
      "<iframe src='https://www.google.com/recaptcha/api2/anchor?k=site-key'></iframe>";
    const badge = document.querySelector("iframe") as HTMLIFrameElement;
    badge.getBoundingClientRect = () => ({ width: 0, height: 0 }) as DOMRect;
    const { cdp } = fakeCdp();
    const result = await checkChallengeOrLogin({ cdp, step: step(), allowedOrigins: [] });
    expect(result.ok).toBe(true);
  });

  it("a visible password field on a page that is not the step is login_required", async () => {
    document.body.innerHTML =
      "<form><input id='pw' name='password' type='password'></form>";
    const { cdp } = fakeCdp();
    const result = await checkChallengeOrLogin({
      cdp,
      step: step({ action: "click", targets: [{ by: "role", role: "button", name: "Save" }] }),
      allowedOrigins: [],
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.event).toBe("login_required");
      expect(result.reason).toMatch(/password/);
    }
  });

  it("does not flag the step's own password field", async () => {
    document.body.innerHTML = "<input id='pw' data-testid='pw' type='password'>";
    const { cdp } = fakeCdp();
    const result = await checkChallengeOrLogin({
      cdp,
      step: step({ action: "fill", targets: [{ by: "testid", value: "pw" }] }),
      allowedOrigins: [],
    });
    expect(result.ok).toBe(true);
  });

  // --- PL-067: warning pages -------------------------------------------------

  it.each([
    "unusual activity",
    "we've restricted",
    "too many requests",
    "verify you're human",
    "temporarily blocked",
    "security check",
  ])("a visible heading saying '%s' raises the challenge event", async (phrase) => {
    document.body.innerHTML = `<h2 id='head'>${phrase}</h2><p>Some page copy.</p>`;
    const head = document.getElementById("head") as HTMLElement;
    head.getBoundingClientRect = () => ({ width: 300, height: 24 }) as DOMRect;
    const { cdp } = fakeCdp();
    const result = await checkChallengeOrLogin({ cdp, step: step(), allowedOrigins: [] });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.event).toBe("challenge");
  });

  it("a dialog saying the account is temporarily blocked raises the challenge event", async () => {
    document.body.innerHTML =
      "<div role='dialog' id='box'><p>Your account has been temporarily blocked.</p></div>";
    const box = document.getElementById("box") as HTMLElement;
    box.getBoundingClientRect = () => ({ width: 400, height: 200 }) as DOMRect;
    const { cdp } = fakeCdp();
    const result = await checkChallengeOrLogin({ cdp, step: step(), allowedOrigins: [] });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.event).toBe("challenge");
      expect(result.reason).toMatch(/temporarily blocked/);
      expect(result.reason).toMatch(/Resume/);
    }
  });

  it("a page titled Security Verification raises the challenge event", async () => {
    document.title = "Security Verification";
    const { cdp } = fakeCdp();
    const result = await checkChallengeOrLogin({ cdp, step: step(), allowedOrigins: [] });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.event).toBe("challenge");
  });

  it("a job description that merely says 'security check' is not a warning page", async () => {
    // Only page-level markers count: the title, a heading, or a dialog. The page's own copy --
    // a LinkedIn job description, say -- is never one, and an input's value is never read.
    document.title = "Operations Manager - job post";
    document.body.innerHTML =
      "<p>You will own physical security check procedures at our depots.</p>" +
      "<input id='q' value='security check'>";
    const { cdp } = fakeCdp();
    const result = await checkChallengeOrLogin({ cdp, step: step(), allowedOrigins: [] });
    expect(result.ok).toBe(true);
  });

  it("a hidden heading with a marker is not a warning page", async () => {
    document.body.innerHTML =
      "<h2 id='head' style='display:none'>unusual activity</h2><p>ordinary page</p>";
    const head = document.getElementById("head") as HTMLElement;
    head.getBoundingClientRect = () => ({ width: 0, height: 0 }) as DOMRect;
    const { cdp } = fakeCdp();
    const result = await checkChallengeOrLogin({ cdp, step: step(), allowedOrigins: [] });
    expect(result.ok).toBe(true);
  });
});

describe("checkDestructive", () => {
  it("a destructive control without the flag is refused", () => {
    const { cdp } = fakeCdp();
    const result = checkDestructive({ cdp, step: step({ name: "Delete account" }), allowedOrigins: [] });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toMatch(/delete/i);
      expect(result.reason).toMatch(/Delete account/);
    }
  });

  it("passes when the step set allow_destructive_control", () => {
    const { cdp } = fakeCdp();
    const optedIn: ExecutorStep = step({ name: "Delete account" });
    (optedIn as { allow_destructive_control?: boolean }).allow_destructive_control = true;
    const result = checkDestructive({ cdp, step: optedIn, allowedOrigins: [] });
    expect(result.ok).toBe(true);
  });

  it("only a click is judged", () => {
    const { cdp } = fakeCdp();
    const result = checkDestructive({
      cdp,
      step: step({ action: "fill", name: "Delete account" }),
      allowedOrigins: [],
    });
    expect(result.ok).toBe(true);
  });

  it("a dropdown option is never destructive, whatever its name says", () => {
    // PL-063, on Odoo: picking the product "Noise-Cancelling Headset" from an invoice line's
    // autocomplete parked the row as a "cancel" control. Choosing an entry from a list deletes,
    // cancels, refunds and pays nothing; the button that would is judged on its own click.
    const { cdp } = fakeCdp();
    const ctx = { cdp, step: step({ name: "Click Noise-Cancelling Headset" }), allowedOrigins: [] };
    expect(checkDestructive(ctx, "[PL-AUD-8001] Noise-Cancelling Headset", "option").ok).toBe(true);
    expect(checkDestructive(ctx, "Cancel order", "button").ok).toBe(false);
    expect(checkDestructive(ctx, "Cancel order").ok).toBe(false);
  });

  it("the destructive match is a word prefix, so Payment is refused", () => {
    expect(destructiveMatch("Payment")).toBe("pay");
    expect(destructiveMatch("Cancel order")).toBe("cancel");
    expect(destructiveMatch("Transfer funds")).toBe("transfer");
    expect(destructiveMatch("Refund the customer")).toBe("refund");
    expect(destructiveMatch("Refresh")).toBeNull();

    const { cdp } = fakeCdp();
    const result = checkDestructive({ cdp, step: step({ name: "Payment" }), allowedOrigins: [] });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toMatch(/pay/i);
  });
});

describe("classifyAttachError", () => {
  it("a policy blocked attach becomes the readable policy message", () => {
    expect(classifyAttachError("cannot attach the debugger: Host access is restricted by policy")).toBe(
      "policy_blocked",
    );
    expect(classifyAttachError("Screenshot capture is restricted by policy")).toBe("policy_blocked");
    expect(POLICY_BLOCKED_MESSAGE).toBe("your administrator's Chrome policy blocks ProcessLens");
    // Anything else keeps today's behaviour.
    expect(classifyAttachError("Cannot access a chrome:// URL")).toBeNull();
    expect(classifyAttachError("Another debugger is already attached")).toBeNull();
  });
});

describe("recheckExpiry", () => {
  it("refuses once the command has expired", async () => {
    const result = await recheckExpiry(1_000, () => 1_000);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toMatch(/expired/);
  });

  it("passes while the command is still current", async () => {
    expect((await recheckExpiry(1_000, () => 999)).ok).toBe(true);
  });
});
