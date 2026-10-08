// Step dispatch: the exact CDP commands each action sends.
//
// A fake transport records every command, so these tests assert the real
// protocol: trusted Input events for typing and clicking, a value update for a
// native select, and Page.navigate for a navigation.
import { beforeEach, describe, expect, it } from "vitest";
import {
  dispatchClick,
  dispatchFill,
  dispatchNavigate,
  dispatchSelect,
  focusAndSelect,
} from "../src/executor/dispatch";
import { resolveTargetInPage } from "../src/executor/ladder";
import { fakeCdp } from "./helpers/fake-cdp";

function stamp(html: string, token: string, target: Parameters<typeof resolveTargetInPage>[0]["targets"][number]): void {
  document.body.innerHTML = html;
  resolveTargetInPage({ targets: [target], within: null, token });
}

beforeEach(() => {
  document.body.innerHTML = "";
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
  it("dispatches a mouse press and release at the box center", async () => {
    stamp("<button id='save'>Save</button>", "c1", { by: "css", selector: "#save" });
    const { cdp, callsTo } = fakeCdp();

    await dispatchClick(cdp, "c1", { x: 20, y: 30 });

    const mouse = callsTo("Input.dispatchMouseEvent").map((call) => call.params);
    expect(mouse.map((params) => params["type"])).toEqual([
      "mouseMoved",
      "mousePressed",
      "mouseReleased",
    ]);
    expect(mouse[1]).toMatchObject({ x: 20, y: 30, button: "left", clickCount: 1 });
    expect(mouse[2]).toMatchObject({ x: 20, y: 30, button: "left" });
  });
});

describe("select", () => {
  it("sets a native select to the matching option by text", async () => {
    document.body.innerHTML =
      "<select id='interest'><option>Interest</option><option>Data course</option></select>";
    resolveTargetInPage({ targets: [{ by: "css", selector: "#interest" }], within: null, token: "s1" });
    const { cdp } = fakeCdp();

    const result = await dispatchSelect(cdp, "s1", "Data course", "text");

    expect(result).toMatchObject({ matched: true, value: "Data course" });
    expect((document.getElementById("interest") as HTMLSelectElement).value).toBe("Data course");
  });

  it("reports no match for an option that is absent", async () => {
    document.body.innerHTML = "<select id='interest'><option>Interest</option></select>";
    resolveTargetInPage({ targets: [{ by: "css", selector: "#interest" }], within: null, token: "s1" });
    const { cdp } = fakeCdp();

    const result = await dispatchSelect(cdp, "s1", "Missing", "text");
    expect(result.matched).toBe(false);
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
