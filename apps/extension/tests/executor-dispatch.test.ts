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
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

describe("fill", () => {
  it("focuses, clears, and types one key event pair per character", async () => {
    stamp("<input id='email'>", "f1", { by: "css", selector: "#email" });
    const { cdp, callsTo } = fakeCdp();

    await dispatchFill(cdp, "f1", "ab");

    const keys = callsTo("Input.dispatchKeyEvent");
    expect(keys).toHaveLength(2 + 2 * 2); // one Backspace pair, then one pair per character
    expect(keys[0]?.params).toMatchObject({ type: "keyDown", key: "Backspace" });
    expect(keys[1]?.params).toMatchObject({ type: "keyUp", key: "Backspace" });
    expect(keys[2]?.params).toMatchObject({ type: "keyDown", text: "a" });
    expect(keys[4]?.params).toMatchObject({ type: "keyDown", text: "b" });
  });

  it("does not clear when clear_first is false", async () => {
    stamp("<input id='email'>", "f1", { by: "css", selector: "#email" });
    const { cdp, callsTo } = fakeCdp();

    await dispatchFill(cdp, "f1", "a", { clearFirst: false });

    const keys = callsTo("Input.dispatchKeyEvent");
    expect(keys).toHaveLength(2);
    expect(keys[0]?.params).toMatchObject({ type: "keyDown", text: "a" });
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

    await expect(dispatchClick(cdp, "c1")).rejects.toThrow(/no clickable area/);
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
  });

  it("fails when the hit test lands on nothing", async () => {
    stamp("<button id='save'>Save</button>", "c1", { by: "css", selector: "#save" });
    restoreLayout = installLayout({ hit: "none" });
    const { cdp, callsTo } = fakeCdp();

    await expect(dispatchClick(cdp, "c1")).rejects.toThrow(/hit test found no element/);
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
  });

  it("fails when the box center is covered by another element", async () => {
    stamp("<button id='save'>Save</button>", "c1", { by: "css", selector: "#save" });
    restoreLayout = installLayout({ hit: "other" });
    const { cdp, callsTo } = fakeCdp();

    await expect(dispatchClick(cdp, "c1")).rejects.toThrow(/covered by/);
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
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
  it("navigates the tab with Page.navigate", async () => {
    const { cdp, callsTo } = fakeCdp();
    await dispatchNavigate(cdp, "https://app.example.com/records/new");
    expect(callsTo("Page.navigate")[0]?.params).toMatchObject({
      url: "https://app.example.com/records/new",
    });
  });
});
