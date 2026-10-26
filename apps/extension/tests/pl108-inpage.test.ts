// PL-108: the in-page dispatcher, exercised against a real jsdom page through the fake CDP.
//
// These are ordinary DOM events (isTrusted=false), so the test is the same page the executor would
// drive; only the transport is a fake.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resolveTargetInPage } from "../src/executor/ladder";
import {
  dialogOverrideExpression,
  inPageClick,
  inPageFill,
  inPagePress,
  inPageSelect,
  inPageUpload,
} from "../src/executor/inpage";
import { fakeCdp } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

function stamp(html: string, token = "t1"): void {
  document.body.innerHTML = html;
  resolveTargetInPage({ targets: [{ by: "css", selector: "#field" }], within: null, token });
}

beforeEach(() => {
  document.body.innerHTML = "";
  restoreLayout = installLayout();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

describe("in-page click", () => {
  it("dispatches the full pointer and mouse sequence at the centre", async () => {
    stamp("<button id='field'>Save</button>");
    const order: string[] = [];
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      document.getElementById("field")!.addEventListener(type, () => order.push(type));
    }
    const { cdp } = fakeCdp();

    const box = await inPageClick(cdp, "t1");

    expect(box.ok).toBe(true);
    expect(order).toEqual(["pointerdown", "mousedown", "pointerup", "mouseup", "click"]);
    // Coordinates are the box centre the layout stub reports (10,10 120x24).
    expect(box.x).toBe(70);
    expect(box.y).toBe(22);
  });

  it("parks with no event when the target is covered", async () => {
    stamp("<button id='field'>Save</button>");
    restoreLayout?.();
    restoreLayout = installLayout({ hit: "other" });
    const { cdp } = fakeCdp();
    let fired = false;
    document.getElementById("field")!.addEventListener("mousedown", () => {
      fired = true;
    });

    await expect(inPageClick(cdp, "t1")).rejects.toThrow(/covered by/);
    expect(fired).toBe(false);
  });
});

describe("in-page fill", () => {
  it("sets a field's value React-safe and fires input and change", async () => {
    stamp("<input id='field'>");
    const events: string[] = [];
    const field = document.getElementById("field") as HTMLInputElement;
    field.addEventListener("input", () => events.push("input"));
    field.addEventListener("change", () => events.push("change"));
    const { cdp } = fakeCdp();

    await inPageFill(cdp, "t1", "hello@example.test", { clearFirst: true });

    expect(field.value).toBe("hello@example.test");
    expect(events).toEqual(["input", "input", "change"]); // clear, set, change
  });

  it("refuses a password field", async () => {
    stamp("<input id='field' type='password'>");
    const { cdp } = fakeCdp();

    await expect(inPageFill(cdp, "t1", "secret")).rejects.toThrow(/password/);
  });

  it("fills a contenteditable body", async () => {
    stamp("<div id='field' contenteditable='true'></div>");
    const { cdp } = fakeCdp();

    await inPageFill(cdp, "t1", "a note");

    expect(document.getElementById("field")!.textContent).toContain("a note");
  });
});

describe("in-page press", () => {
  it("fires keydown and keyup, and asks the form to submit on Enter", async () => {
    stamp("<form><input id='field'></form>");
    const keys: string[] = [];
    const field = document.getElementById("field") as HTMLInputElement;
    field.addEventListener("keydown", (event) => keys.push(`down:${(event as KeyboardEvent).key}`));
    field.addEventListener("keyup", (event) => keys.push(`up:${(event as KeyboardEvent).key}`));
    const submit = vi.fn();
    const form = field.form!;
    (form as unknown as { requestSubmit: () => void }).requestSubmit = submit;
    const { cdp } = fakeCdp();

    await inPagePress(cdp, "t1", "Enter");

    expect(keys).toEqual(["down:Enter", "up:Enter"]);
    expect(submit).toHaveBeenCalledTimes(1);
  });
});

describe("in-page select", () => {
  it("chooses a native option and fires change", async () => {
    stamp(
      "<select id='field'><option value='a'>Alpha</option><option value='b'>Beta</option></select>",
    );
    const select = document.getElementById("field") as HTMLSelectElement;
    const changed = vi.fn();
    select.addEventListener("change", changed);
    const { cdp } = fakeCdp();

    const answer = await inPageSelect(cdp, "t1", "Beta", "text");

    expect(answer).toMatchObject({ matched: true, value: "b" });
    expect(select.value).toBe("b");
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it("answers custom for a control that is not a native select", async () => {
    stamp("<div id='field' role='combobox'></div>");
    const { cdp } = fakeCdp();

    expect(await inPageSelect(cdp, "t1", "Beta")).toMatchObject({ matched: false, custom: true });
  });
});

describe("in-page upload", () => {
  it("refuses gracefully when the page cannot build a file list", async () => {
    stamp("<input id='field' type='file'>");
    const { cdp } = fakeCdp();
    const hasDataTransfer = typeof (globalThis as { DataTransfer?: unknown }).DataTransfer === "function";

    if (hasDataTransfer) {
      await expect(
        inPageUpload(cdp, "t1", [{ name: "a.txt", type: "text/plain", data: "aGk=" }]),
      ).resolves.toBeUndefined();
    } else {
      await expect(
        inPageUpload(cdp, "t1", [{ name: "a.txt", type: "text/plain", data: "aGk=" }]),
      ).rejects.toThrow(/without the debugger/);
    }
  });
});

describe("the dialog override", () => {
  it("captures alert, confirm and prompt instead of showing them", async () => {
    const { cdp } = fakeCdp();
    await (cdp.send("Runtime.evaluate", {
      expression: dialogOverrideExpression(),
      returnByValue: true,
    }) as Promise<unknown>);

    window.alert("careful");
    const confirmed = window.confirm("sure?");
    const answer = window.prompt("name?", "fallback");

    const dialogs = (window as unknown as { __plDialogs: { kind: string; message: string }[] })
      .__plDialogs;
    expect(dialogs.map((entry) => entry.kind)).toEqual(["alert", "confirm", "prompt"]);
    expect(dialogs[0]?.message).toBe("careful");
    expect(confirmed).toBe(false);
    expect(answer).toBe("fallback");
    (window as unknown as { __plRestoreDialogs?: () => void }).__plRestoreDialogs?.();
    delete (window as unknown as { __plDialogsInstalled?: boolean }).__plDialogsInstalled;
  });
});
