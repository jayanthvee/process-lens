// PL-018 §3.4: the executor's `press` half.
//
// A press is two real CDP key events and nothing else — no `char`, no mouse. These tests assert the
// exact protocol (the fake transport records every command), the gate order a withdrawn command
// depends on, and that `performStep` resolves the target and reports the press like every other
// action.
import { beforeEach, describe, expect, it } from "vitest";
import { dispatchPress } from "../src/executor/dispatch";
import { validateRecipe } from "../src/executor/executor";
import { resolveTargetInPage } from "../src/executor/ladder";
import { performStep } from "../src/executor/step-runner";
import { ExecutorError, type ExecutorStep } from "../src/executor/types";
import { fakeCdp } from "./helpers/fake-cdp";

beforeEach(() => {
  document.body.innerHTML = "";
});

/** The two events a press sends, in order. */
function keyEvents(cdp: ReturnType<typeof fakeCdp>): Array<Record<string, unknown>> {
  return cdp.callsTo("Input.dispatchKeyEvent").map((call) => call.params);
}

describe("dispatchPress", () => {
  it("sends a keyDown then a keyUp for Enter: code, virtual key code, and the keydown's text", async () => {
    const fake = fakeCdp();

    await dispatchPress(fake.cdp, "t1", "Enter");

    const keys = keyEvents(fake);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toMatchObject({
      type: "keyDown",
      key: "Enter",
      code: "Enter",
      windowsVirtualKeyCode: 13,
      nativeVirtualKeyCode: 13,
      text: "\r",
    });
    expect(keys[1]).toMatchObject({
      type: "keyUp",
      key: "Enter",
      code: "Enter",
      windowsVirtualKeyCode: 13,
      nativeVirtualKeyCode: 13,
    });
    // A real keyup carries no text.
    expect(keys[1]?.["text"]).toBeUndefined();
    // Never a `char`: the widget's handler sees the pair.
    expect(keys.some((params) => params["type"] === "char")).toBe(false);
  });

  it("gives Tab its own code, key code, and tab text", async () => {
    const fake = fakeCdp();

    await dispatchPress(fake.cdp, "t1", "Tab");

    const keys = keyEvents(fake);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toMatchObject({ type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9, text: "\t" });
    expect(keys[1]).toMatchObject({ type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  });

  it("sends Escape, Backspace, and Delete with their own key codes", async () => {
    const fake = fakeCdp();

    await dispatchPress(fake.cdp, "t1", "Escape");
    await dispatchPress(fake.cdp, "t1", "Backspace");
    await dispatchPress(fake.cdp, "t1", "Delete");

    const keys = keyEvents(fake);
    expect(keys.map((params) => [params["key"], params["code"], params["windowsVirtualKeyCode"]])).toEqual([
      ["Escape", "Escape", 27],
      ["Escape", "Escape", 27],
      ["Backspace", "Backspace", 8],
      ["Backspace", "Backspace", 8],
      ["Delete", "Delete", 46],
      ["Delete", "Delete", 46],
    ]);
    // Only Enter and Tab type a character; Escape/Backspace/Delete keydowns carry no text.
    expect(keys.every((params) => params["text"] === undefined)).toBe(true);
  });

  it("sends Control+Enter with modifiers 2 and Meta+Enter with modifiers 4, both keyed Enter", async () => {
    const fake = fakeCdp();

    await dispatchPress(fake.cdp, "t1", "Control+Enter");
    await dispatchPress(fake.cdp, "t1", "Meta+Enter");

    const keys = keyEvents(fake);
    expect(keys[0]).toMatchObject({ type: "keyDown", key: "Enter", code: "Enter", modifiers: 2, text: "\r" });
    expect(keys[1]).toMatchObject({ type: "keyUp", key: "Enter", code: "Enter", modifiers: 2 });
    expect(keys[2]).toMatchObject({ type: "keyDown", key: "Enter", code: "Enter", modifiers: 4, text: "\r" });
    expect(keys[3]).toMatchObject({ type: "keyUp", key: "Enter", code: "Enter", modifiers: 4 });
  });

  it("refuses a key it cannot name correctly, sending nothing", async () => {
    const fake = fakeCdp();

    const error = await dispatchPress(fake.cdp, "t1", "F13").catch((thrown: unknown) => thrown);

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).disposition).toBe("park");
    expect((error as ExecutorError).message).toContain("F13");
    expect(fake.calls).toHaveLength(0);
  });

  it("a gate that throws on the second call sends no keyUp", async () => {
    const fake = fakeCdp();
    let seen = 0;

    const error = await dispatchPress(fake.cdp, "t1", "Enter", undefined, {
      beforeInput: () => {
        seen += 1;
        if (seen === 2) throw new Error("the command was withdrawn");
      },
    }).catch((thrown: unknown) => thrown);

    expect((error as Error).message).toBe("the command was withdrawn");
    const keys = keyEvents(fake);
    expect(keys).toHaveLength(1);
    expect(keys[0]).toMatchObject({ type: "keyDown" });
  });

  it("gates each send: beforeInput twice and onFirstInput once, before the send they precede", async () => {
    const events: string[] = [];
    const fake = fakeCdp((method, params) => {
      events.push(`${method}:${String(params["type"] ?? "")}`);
    });

    await dispatchPress(fake.cdp, "t1", "Enter", undefined, {
      beforeInput: () => events.push("beforeInput"),
      onFirstInput: () => events.push("onFirstInput"),
    });

    expect(events).toEqual([
      "beforeInput",
      "onFirstInput",
      "Input.dispatchKeyEvent:keyDown",
      "beforeInput",
      "Input.dispatchKeyEvent:keyUp",
    ]);
  });
});

describe("performStep with a press", () => {
  it("resolves the target, sends the pair, and reports the press done", async () => {
    document.body.innerHTML = "<button id='accept'>Accept</button>";
    resolveTargetInPage({ targets: [{ by: "css", selector: "#accept" }], within: null, token: "t1" });
    const fake = fakeCdp();
    const step = {
      id: "accept",
      action: "press",
      key: "Enter",
      targets: [{ by: "css", selector: "#accept" }],
    } as unknown as ExecutorStep;

    const report = await performStep(step, { row: {} }, undefined, { cdp: fake.cdp });

    expect(report).toMatchObject({ id: "accept", action: "press", outcome: "done", rung: 4, by: "css" });
    const keys = keyEvents(fake);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toMatchObject({ type: "keyDown", key: "Enter", text: "\r" });
    expect(keys[1]).toMatchObject({ type: "keyUp", key: "Enter" });
    // A press acts on the element itself: no box is measured and no mouse event is sent.
    expect(fake.callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
  });

  it("honours a preset target instead of resolving it a second time", async () => {
    document.body.innerHTML = "<button id='accept'>Accept</button>";
    const fake = fakeCdp();
    const step = {
      id: "accept",
      action: "press",
      key: "Tab",
      targets: [{ by: "css", selector: "#accept" }],
    } as unknown as ExecutorStep;

    await performStep(step, { row: {} }, undefined, { cdp: fake.cdp }, undefined, {
      ok: true,
      token: "preset-1",
      rung: 1,
      by: "role",
      tag: "button",
      name: "Accept",
    });

    // The guard phase already resolved and hit-tested this element, so nothing is resolved again.
    expect(fake.callsTo("Runtime.evaluate")).toHaveLength(0);
    expect(keyEvents(fake)).toHaveLength(2);
  });

  it("parks, naming the step, when the press target cannot be resolved", async () => {
    document.body.innerHTML = "<button id='accept'>Accept</button>";
    const fake = fakeCdp();
    const step = {
      id: "accept",
      action: "press",
      key: "Enter",
      targets: [{ by: "css", selector: "#missing" }],
    } as unknown as ExecutorStep;

    const error = await performStep(step, { row: {} }, undefined, { cdp: fake.cdp }).catch(
      (thrown: unknown) => thrown,
    );

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).stepId).toBe("accept");
    expect((error as ExecutorError).disposition).toBe("park");
    expect(fake.callsTo("Input.dispatchKeyEvent")).toHaveLength(0);
  });
});

describe("the run-creation gate for press", () => {
  function recipe(step: Record<string, unknown>): Parameters<typeof validateRecipe>[0] {
    return { format_version: 1, meta: { name: "t" }, steps: [step] } as unknown as Parameters<
      typeof validateRecipe
    >[0];
  }

  it("accepts a well-formed press", () => {
    expect(
      validateRecipe(
        recipe({
          id: "accept",
          action: "press",
          key: "Enter",
          targets: [{ by: "role", role: "option", name: "Ann" }],
        }),
      ),
    ).toEqual([]);
  });

  it("refuses a press with no key", () => {
    const problems = validateRecipe(
      recipe({ id: "accept", action: "press", targets: [{ by: "role", role: "option", name: "Ann" }] }),
    );
    expect(problems).toEqual(["step accept is a press with no key"]);
  });

  it("refuses a press whose key is not a string", () => {
    const problems = validateRecipe(
      recipe({
        id: "accept",
        action: "press",
        key: 13,
        targets: [{ by: "role", role: "option", name: "Ann" }],
      }),
    );
    expect(problems).toEqual(["step accept is a press with no key"]);
  });

  it("refuses a press with no target ladder", () => {
    const problems = validateRecipe(recipe({ id: "accept", action: "press", key: "Enter", targets: [] }));
    expect(problems).toEqual(["step accept has action press but no target ladder"]);
  });
});
