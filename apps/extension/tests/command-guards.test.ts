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
