// Step dispatch: the exact CDP commands each action sends.
//
// A fake transport records every command, so these tests assert the real
// protocol: trusted Input events for typing and clicking, a value update for a
// native select, and Page.navigate for a navigation.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  dispatchClick,
  dispatchFill,
  dispatchNavigate,
  dispatchSelect,
  focusAndSelect,
  measureTarget,
  prepareField,
  resetInputClock,
} from "../src/executor/dispatch";
import { resolveTargetInPage } from "../src/executor/ladder";
import { fakeCdp } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

function stamp(html: string, token: string, target: Parameters<typeof resolveTargetInPage>[0]["targets"][number]): void {
  document.body.innerHTML = html;
  resolveTargetInPage({ targets: [target], within: null, token });
}

beforeEach(() => {
  document.body.innerHTML = "";
  // The post-input quiet window is session state: a fill or a press in one test must not make the
  // next test's click wait.
  resetInputClock();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

describe("fill", () => {
  it("focuses, clears, and inserts the whole value with one Input.insertText", async () => {
    stamp("<input id='email'>", "f1", { by: "css", selector: "#email" });
    const { cdp, callsTo } = fakeCdp(undefined, { simulateInput: true });

    await dispatchFill(cdp, "f1", "ab");

    // Requirement 5: one insert keeps `\n` and `\t` as characters, not Enter or Tab.
    const keys = callsTo("Input.dispatchKeyEvent");
    expect(keys).toHaveLength(2); // the Backspace pair that clears the field
    expect(keys[0]?.params).toMatchObject({ type: "keyDown", key: "Backspace" });
    expect(keys[1]?.params).toMatchObject({ type: "keyUp", key: "Backspace" });
    const inserts = callsTo("Input.insertText");
    expect(inserts).toHaveLength(1);
    expect(inserts[0]?.params).toMatchObject({ text: "ab" });
  });

  it("does not clear when clear_first is false", async () => {
    stamp("<input id='email'>", "f1", { by: "css", selector: "#email" });
    const { cdp, callsTo } = fakeCdp(undefined, { simulateInput: true });

    await dispatchFill(cdp, "f1", "a", { clearFirst: false });

    // With no clearing there is no Backspace pair at all; the text is still one insert.
    expect(callsTo("Input.dispatchKeyEvent")).toHaveLength(0);
    const inserts = callsTo("Input.insertText");
    expect(inserts).toHaveLength(1);
    expect(inserts[0]?.params).toMatchObject({ text: "a" });
  });

  it("refuses to type into a password field", async () => {
    stamp("<input id='pw' type='password'>", "f1", { by: "css", selector: "#pw" });
    const { cdp, callsTo } = fakeCdp();

    await expect(dispatchFill(cdp, "f1", "secret")).rejects.toThrow(/password/);
    expect(callsTo("Input.dispatchKeyEvent")).toHaveLength(0);
  });

  it("refuses a field whose autocomplete marks it as a password", async () => {
    stamp("<input id='pw' type='text' autocomplete='current-password'>", "f1", {
      by: "css",
      selector: "#pw",
    });
    const { cdp, callsTo } = fakeCdp();

    await expect(dispatchFill(cdp, "f1", "secret")).rejects.toThrow(/password/);
    expect(callsTo("Input.dispatchKeyEvent")).toHaveLength(0);
  });

  it("reports a password field from prepareField", async () => {
    stamp("<input id='pw' type='password'>", "f1", { by: "css", selector: "#pw" });
    const { cdp } = fakeCdp();
    expect(await prepareField(cdp, "f1")).toMatchObject({ ok: false, reason: "password" });
  });

  it("throws when the target marker is gone", async () => {
    document.body.innerHTML = "<input id='email'>";
    const { cdp } = fakeCdp();
    await expect(dispatchFill(cdp, "missing", "a")).rejects.toThrow(/could not be focused/);
  });

  it("focuses and selects the field", async () => {
    document.body.innerHTML = "<input id='email' value='old'>";
    const { cdp, callsTo } = fakeCdp();
    const ok = await focusAndSelect(cdp, "missing");
    expect(ok).toBe(false);
    expect(callsTo("Runtime.evaluate")).toHaveLength(1);
  });
});

describe("click", () => {
  it("scrolls, re-measures, hit-tests, and dispatches at the measured center", async () => {
    stamp("<button id='save'>Save</button>", "c1", { by: "css", selector: "#save" });
    restoreLayout = installLayout({ left: 10, top: 10, width: 120, height: 24 });
    const { cdp, callsTo } = fakeCdp();

    const box = await dispatchClick(cdp, "c1");

    expect(box).toMatchObject({ ok: true, x: 70, y: 22 });
    const mouse = callsTo("Input.dispatchMouseEvent").map((call) => call.params);
    expect(mouse.map((params) => params["type"])).toEqual([
      "mouseMoved",
      "mousePressed",
      "mouseReleased",
    ]);
    expect(mouse[1]).toMatchObject({ x: 70, y: 22, button: "left", clickCount: 1 });
    expect(mouse[2]).toMatchObject({ x: 70, y: 22, button: "left" });
  });

  it("fails when the box has no clickable area", async () => {
    stamp("<button id='save'>Save</button>", "c1", { by: "css", selector: "#save" });
    restoreLayout = installLayout({ width: 0, height: 0 });
    const { cdp, callsTo } = fakeCdp();

    // A short settle deadline: the box never becomes clickable, and the wait must not run to its
    // full default before parking.
    await expect(dispatchClick(cdp, "c1", undefined, undefined, 40)).rejects.toThrow(
      /no clickable area/,
    );
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
  });

  it("fails when the hit test lands on nothing", async () => {
    stamp("<button id='save'>Save</button>", "c1", { by: "css", selector: "#save" });
    restoreLayout = installLayout({ hit: "none" });
    const { cdp, callsTo } = fakeCdp();

    await expect(dispatchClick(cdp, "c1", undefined, undefined, 40)).rejects.toThrow(
      /hit test found no element/,
    );
    // The final check refuses; the move that precedes it is the only mouse event -- never a press.
    const mouseTypes = callsTo("Input.dispatchMouseEvent").map((call) => call.params["type"]);
    expect(mouseTypes).not.toContain("mousePressed");
    expect(mouseTypes).not.toContain("mouseReleased");
  });

  it("fails when the box center is covered by another element", async () => {
    stamp("<button id='save'>Save</button>", "c1", { by: "css", selector: "#save" });
    restoreLayout = installLayout({ hit: "other" });
    const { cdp, callsTo } = fakeCdp();

    await expect(dispatchClick(cdp, "c1", undefined, undefined, 40)).rejects.toThrow(/covered by/);
    // The final check refuses; the move that precedes it is the only mouse event -- never a press.
    const mouseTypes = callsTo("Input.dispatchMouseEvent").map((call) => call.params["type"]);
    expect(mouseTypes).not.toContain("mousePressed");
    expect(mouseTypes).not.toContain("mouseReleased");
  });

  it("measureTarget fails when the marker is gone", async () => {
    restoreLayout = installLayout();
    const { cdp } = fakeCdp();
    await expect(measureTarget(cdp, "missing")).rejects.toThrow(/not clickable/);
  });
});

describe("select", () => {
  it("sets a native select to the option whose text matches exactly", async () => {
    document.body.innerHTML =
      "<select id='interest'><option>Interest</option><option>Data course</option></select>";
    resolveTargetInPage({ targets: [{ by: "css", selector: "#interest" }], within: null, token: "s1" });
    const { cdp } = fakeCdp();

    const result = await dispatchSelect(cdp, "s1", "Data course", "text");

    expect(result).toMatchObject({ matched: true, value: "Data course" });
    expect((document.getElementById("interest") as HTMLSelectElement).value).toBe("Data course");
  });

  it("uses a substring only when exactly one option contains it", async () => {
    document.body.innerHTML =
      "<select id='interest'><option>Intro to data</option><option>Sales</option></select>";
    resolveTargetInPage({ targets: [{ by: "css", selector: "#interest" }], within: null, token: "s1" });
    const { cdp } = fakeCdp();

    const result = await dispatchSelect(cdp, "s1", "data", "text");
    expect(result).toMatchObject({ matched: true, value: "Intro to data" });
  });

  it("fails when more than one option contains the text", async () => {
    document.body.innerHTML =
      "<select id='interest'><option>Data course</option><option>Data science</option></select>";
    resolveTargetInPage({ targets: [{ by: "css", selector: "#interest" }], within: null, token: "s1" });
    const { cdp } = fakeCdp();

    const result = await dispatchSelect(cdp, "s1", "Data", "text");
    expect(result.matched).toBe(false);
    expect(result.reason).toMatch(/more than one option/);
  });

  it("reports an absent option", async () => {
    document.body.innerHTML = "<select id='interest'><option>Interest</option></select>";
    resolveTargetInPage({ targets: [{ by: "css", selector: "#interest" }], within: null, token: "s1" });
    const { cdp } = fakeCdp();

    const result = await dispatchSelect(cdp, "s1", "Missing", "text");
    expect(result.matched).toBe(false);
    expect(result.reason).toMatch(/no option matched/);
  });

  it("falls back to a value match", async () => {
    document.body.innerHTML =
      "<select id='interest'><option value='data'>Data course</option></select>";
    resolveTargetInPage({ targets: [{ by: "css", selector: "#interest" }], within: null, token: "s1" });
    const { cdp } = fakeCdp();

    const result = await dispatchSelect(cdp, "s1", "data", "value");
    expect(result).toMatchObject({ matched: true, value: "data" });
  });
});

describe("navigate", () => {
  /**
   * A transport that models a real browser's answer to a navigate: the reply the browser gave, and
   * the URL the tab actually holds -- which, for a reply with no loaderId, may be the page the
   * step was sent from. `after` is what the tab holds once a navigation has really happened.
   */
  function navigatingCdp(
    before: string,
    after: string,
    reply: Record<string, unknown> = { frameId: "F1", loaderId: "L1" },
    arrivesOn = 1,
  ) {
    let navigations = 0;
    const fake = fakeCdp((method, params) => {
      if (method === "Page.navigate") {
        navigations += 1;
        return reply;
      }
      if (method === "Runtime.evaluate" && String(params["expression"] ?? "").trim() === "location.href") {
        return { result: { value: navigations >= arrivesOn ? after : before } };
      }
      return undefined;
    });
    // A loaderId in the reply means the load watch is armed; settle it the way a page that has
    // already loaded would.
    if (reply["loaderId"]) {
      setTimeout(() => {
        fake.emit("Page.frameNavigated", { frame: { loaderId: reply["loaderId"] } });
        fake.emit("Page.loadEventFired", {});
      }, 0);
    }
    return fake;
  }

  it("navigates the tab with Page.navigate", async () => {
    const { cdp, callsTo } = navigatingCdp(
      "https://app.example.com/records",
      "https://app.example.com/records/new",
    );
    await dispatchNavigate(cdp, "https://app.example.com/records/new");
    expect(callsTo("Page.navigate")[0]?.params).toMatchObject({
      url: "https://app.example.com/records/new",
    });
  });

  it("refuses when the tab does not show the page it was sent to", async () => {
    // PL-027, on Odoo: the reply carried no loaderId (a same-document navigation, so the previous
    // document stayed), the step reported success, and the step after it resolved its target
    // against the page the demonstration had left. The URL is the proof, and its absence is a
    // refusal -- with the URL it found, so the failure says what happened.
    const { cdp, callsTo } = navigatingCdp(
      "https://app.example.com/records/18",
      "https://app.example.com/records/18",
      { frameId: "F1", loaderId: "" },
    );

    await expect(
      dispatchNavigate(cdp, "https://app.example.com/records", 600),
    ).rejects.toThrow(/did not arrive.*records\/18/);
    // The same-document reply earned the page a second real navigation before the refusal.
    expect(callsTo("Page.navigate")).toHaveLength(2);
  });

  it("succeeds on the second attempt when the first reply was same-document", async () => {
    const { cdp, callsTo } = navigatingCdp(
      "https://app.example.com/old",
      "https://app.example.com/records/new",
      { frameId: "F1", loaderId: "" },
      2,
    );

    await dispatchNavigate(cdp, "https://app.example.com/records/new", 400);
    expect(callsTo("Page.navigate")).toHaveLength(2);
  });

  it("does not ask twice when the reply replaced the document", async () => {
    // A reply with a loaderId means a new document arrived; a URL that still disagrees is a
    // redirect, not a step to repeat.
    const { cdp, callsTo } = navigatingCdp(
      "https://app.example.com/elsewhere",
      "https://app.example.com/elsewhere",
      { frameId: "F1", loaderId: "L1" },
    );

    await expect(
      dispatchNavigate(cdp, "https://app.example.com/records/new", 600),
    ).rejects.toThrow(/did not arrive/);
    expect(callsTo("Page.navigate")).toHaveLength(1);
  });

  it("accepts a page's own query rewrite, but not a different path", async () => {
    const { cdp } = navigatingCdp(
      "https://app.example.com/records",
      "https://app.example.com/records?view_type=list&action=42",
    );
    await expect(
      dispatchNavigate(cdp, "https://app.example.com/records?view_type=list", 600),
    ).resolves.toBeUndefined();

    const wrong = navigatingCdp(
      "https://app.example.com/records/18",
      "https://app.example.com/records/18",
    );
    await expect(
      dispatchNavigate(wrong.cdp, "https://app.example.com/records", 600),
    ).rejects.toThrow(/did not arrive/);
  });

  it("leaves an alert alone and accepts a beforeunload", async () => {
    const alert = navigatingCdp(
      "https://app.example.com/records",
      "https://app.example.com/records/new",
    );
    await dispatchNavigate(alert.cdp, "https://app.example.com/records/new", 600);
    alert.emit("Page.javascriptDialogOpening", { type: "alert", message: "hi" });
    expect(alert.callsTo("Page.handleJavaScriptDialog")).toHaveLength(0);

    // The transport is bound to one tab -- the tab the run is driving -- so a beforeunload that
    // arrives while a navigate is running is that navigate's own dialog, and it is accepted.
    const beforeunload = navigatingCdp(
      "https://app.example.com/old",
      "https://app.example.com/records/new",
      { frameId: "F1", loaderId: "" },
    );
    const pending = dispatchNavigate(
      beforeunload.cdp,
      "https://app.example.com/records/new",
      400,
    );
    beforeunload.emit("Page.javascriptDialogOpening", { type: "beforeunload" });
    await pending;
    expect(beforeunload.callsTo("Page.handleJavaScriptDialog")[0]?.params).toMatchObject({
      accept: true,
    });
  });
});
