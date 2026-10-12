// Requirement 5 of PL-014: the hardening gaps the ticket names, each with the
// test that proves it was closed.
//
// The fake transport evaluates the page-side expressions in real jsdom, so the
// resolver, the measurement, and the real CDP command sequence all run here;
// only the browser itself is simulated. Where the change is about *refusing*
// something the fake is deliberately hostile: `elementFromPoint` is made to
// answer with an ancestor, `window.top` is made to look like a subframe, and
// `Page.navigate` is made to answer with an `errorText`.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { waitForCondition } from "../src/executor/assertions";
import type { CdpTransport } from "../src/executor/cdp";
import { RunControl } from "../src/executor/control";
import { dispatchClick, dispatchFill, dispatchNavigate } from "../src/executor/dispatch";
import { runRecipe } from "../src/executor/executor";
import { resolveTargetInPage, type ResolveRung } from "../src/executor/ladder";
import { performStep } from "../src/executor/step-runner";
import { ExecutorError, type ExecutorStep } from "../src/executor/types";
import { fakeCdp, type RecordedCall } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

const ORIGIN = "https://app.example.com";

let restoreLayout: (() => void) | null = null;
let restoreStubs: (() => void) | null = null;

beforeEach(() => {
  document.body.innerHTML = "";
  // Clicks measure a box and hit-test it; jsdom has no layout engine.
  restoreLayout = installLayout();
});

afterEach(() => {
  restoreStubs?.();
  restoreStubs = null;
  restoreLayout?.();
  restoreLayout = null;
});

/** Mount markup and let the real resolver stamp the target. */
function stamp(html: string, rung: ResolveRung, token = "t1"): void {
  document.body.innerHTML = html;
  resolveTargetInPage({ targets: [rung], within: null, token });
}

function marked(token = "t1"): Element | null {
  return document.querySelector(`[data-pl-target="${token}"]`);
}

/** The marker wherever the executor put it, whatever token it chose. */
function anyMarked(): Element | null {
  return document.querySelector("[data-pl-target]");
}

/** Make `elementFromPoint` answer with an ancestor of the target. */
function installAncestorHit(ancestor: Element | null): () => void {
  const original = Document.prototype.elementFromPoint;
  Document.prototype.elementFromPoint = function elementFromPoint() {
    return ancestor;
  } as typeof Document.prototype.elementFromPoint;
  return () => {
    Document.prototype.elementFromPoint = original;
  };
}

/** Make `window.top` say this document is a subframe. */
function installSubframe(): () => void {
  const target = window as unknown as Record<string, unknown>;
  const own = Object.getOwnPropertyDescriptor(target, "top");
  Object.defineProperty(target, "top", {
    configurable: true,
    writable: true,
    value: { name: "subframe" },
  });
  return () => {
    if (own) Object.defineProperty(target, "top", own);
    else delete target["top"];
  };
}

/** A transport whose CDP event feed the test drives by hand. */
function eventTransport(navigateReply: Record<string, unknown> = {}): {
  cdp: CdpTransport;
  calls: RecordedCall[];
  emit: (event: string, params: Record<string, unknown>) => void;
} {
  const base = fakeCdp((method) => (method === "Page.navigate" ? navigateReply : undefined));
  const listeners = new Map<string, Array<(params: Record<string, unknown>) => void>>();
  const cdp: CdpTransport = {
    async send(method, params = {}) {
      return base.cdp.send(method, params);
    },
    on(event, handler) {
      const list = listeners.get(event) ?? [];
      list.push(handler);
      listeners.set(event, list);
      return () => {
        listeners.set(event, (listeners.get(event) ?? []).filter((entry) => entry !== handler));
      };
    },
  };
  return {
    cdp,
    calls: base.calls,
    emit: (event, params) => {
      for (const listener of listeners.get(event) ?? []) listener(params);
    },
  };
}

describe("the ladder's order and numbering", () => {
  it("ladder ranks before array order", () => {
    // The recipe lists `css` first; rung 1 (`role`) still wins, and the css rung
    // is never reached.
    document.body.innerHTML =
      "<button id='ranked'>Save</button><div id='by-css'>Not a button</div>";
    const result = resolveTargetInPage({
      targets: [
        { by: "css", selector: "#by-css" },
        { by: "role", role: "button", name: "Save" },
      ],
      within: null,
      token: "t1",
    });

    expect(result.ok).toBe(true);
    expect(result.by).toBe("role");
    expect(result.rung).toBe(1);
    expect(marked()?.id).toBe("ranked");
    expect(result.attempts.map((attempt) => attempt.by)).toEqual(["role"]);
  });

  it("a rung-1 match reports rung 1", () => {
    document.body.innerHTML = "<button id='save'>Save</button>";
    const result = resolveTargetInPage({
      targets: [{ by: "role", role: "button", name: "Save" }],
      within: null,
      token: "t1",
    });

    expect(result.ok).toBe(true);
    // Rung numbers are the recipe format's, 1..5 — not the array index.
    expect(result.rung).toBe(1);
    expect(result.attempts[0]?.rung).toBe(1);

    // And the last rung is 5, not 4.
    document.body.innerHTML = "<div id='wrap'><span id='inner'>Save lead</span></div>";
    const fuzzy = resolveTargetInPage({
      targets: [{ by: "text_fuzzy", value: "Save lead" }],
      within: null,
      token: "t1",
    });
    expect(fuzzy.rung).toBe(5);
    expect(fuzzy.attempts[0]?.rung).toBe(5);
  });

  it("an ambiguous within container fails the rung", () => {
    // Two visible forms answer to the same name: scoping to either one would be
    // a guess, so the rung is skipped and nothing is stamped.
    document.body.innerHTML =
      "<form aria-label='New lead'><button id='s1'>Save</button></form>" +
      "<form aria-label='New lead'><button id='s2'>Save</button></form>";
    const result = resolveTargetInPage({
      targets: [{ by: "role", role: "button", name: "Save" }],
      within: "form:New lead",
      token: "t1",
    });

    expect(result.ok).toBe(false);
    expect(result.marker).toBeNull();
    expect(result.attempts[0]?.reason).toBe('container "form:New lead" is not unique');
    expect(result.attempts[0]?.matches).toBe(0);
    expect(marked()).toBeNull();
  });
});

describe("the click hit test", () => {
  it("an ancestor at the click point is not a hit", async () => {
    stamp(
      "<div id='shell'><button id='save'>Save</button></div>",
      { by: "css", selector: "#save" },
      "c1",
    );
    // The point at the target's center belongs to a wrapper that merely contains
    // the button: a click there would land on the wrapper.
    restoreStubs = installAncestorHit(document.getElementById("shell"));
    const { cdp, callsTo } = fakeCdp();

    const error = await dispatchClick(cdp, "c1").catch((thrown) => thrown);

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).message).toMatch(/covered by <div>/);
    expect((error as ExecutorError).disposition).toBe("park");
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
  });

  it("a click inside an iframe is refused", async () => {
    stamp("<button id='save'>Save</button>", { by: "css", selector: "#save" }, "c1");
    restoreStubs = installSubframe();
    // The measurement must have seen a subframe, or the rest proves nothing.
    expect(window.top === window.self).toBe(false);
    const { cdp, callsTo } = fakeCdp();

    const error = await dispatchClick(cdp, "c1").catch((thrown) => thrown);

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).disposition).toBe("park");
    expect((error as ExecutorError).message).toMatch(/inside a frame/);
    expect(callsTo("Input.dispatchMouseEvent")).toHaveLength(0);
  });
});

describe("fills", () => {
  it("a newline in a fill is a character, not Enter", async () => {
    stamp("<textarea id='notes'></textarea>", { by: "css", selector: "#notes" }, "f1");
    const { cdp, callsTo } = fakeCdp((method, params) => {
      if (method !== "Input.insertText") return undefined;
      // A real insertText replaces the field's selection, which `prepareField`
      // has just made cover the whole value; jsdom does neither on its own.
      (document.getElementById("notes") as HTMLTextAreaElement).value = String(
        params["text"] ?? "",
      );
      return undefined;
    });

    await dispatchFill(cdp, "f1", "line one\nline two");

    const inserts = callsTo("Input.insertText");
    expect(inserts).toHaveLength(1);
    expect(inserts[0]?.params).toMatchObject({ text: "line one\nline two" });
    // The only key events are the clearing Backspace pair: a newline never
    // becomes an Enter keypress, and there is no per-character event at all.
    expect(callsTo("Input.dispatchKeyEvent").map((call) => call.params["key"])).toEqual([
      "Backspace",
      "Backspace",
    ]);
    expect((document.getElementById("notes") as HTMLTextAreaElement).value).toBe(
      "line one\nline two",
    );
  });

  it("clear_first false leaves the existing text in place", async () => {
    stamp("<input id='email' value='existing'>", { by: "css", selector: "#email" }, "f1");
    const { cdp, callsTo } = fakeCdp((method, params) => {
      if (method !== "Input.insertText") return undefined;
      const field = document.getElementById("email") as HTMLInputElement;
      field.value = `${field.value}${String(params["text"] ?? "")}`;
      return undefined;
    });
    // A selection on the field is how a "clear then retype" announces itself:
    // with clear_first false the executor must not select anything.
    const selects: string[] = [];
    const originalSelect = HTMLInputElement.prototype.select;
    HTMLInputElement.prototype.select = function selectSpy(this: HTMLInputElement) {
      selects.push("select");
      return originalSelect.call(this);
    };

    try {
      const error = await dispatchFill(cdp, "f1", "NEW", { clearFirst: false }).catch(
        (thrown) => thrown,
      );

      // Nothing was cleared: not selected, no Backspace, and the text that was
      // already there is still there.
      expect(selects).toHaveLength(0);
      expect(callsTo("Input.dispatchKeyEvent").map((call) => call.params["key"])).not.toContain(
        "Backspace",
      );
      expect((document.getElementById("email") as HTMLInputElement).value).toBe("existingNEW");

      // The field does not hold what was typed, so the fill is refused rather
      // than reported as done — naming both values.
      expect(error).toBeInstanceOf(ExecutorError);
      expect((error as ExecutorError).disposition).toBe("park");
      expect((error as ExecutorError).message).toContain('"NEW"');
      expect((error as ExecutorError).message).toContain('"existingNEW"');
    } finally {
      HTMLInputElement.prototype.select = originalSelect;
    }
  });

  it("a fill whose value does not stick fails", async () => {
    stamp("<input id='email'>", { by: "css", selector: "#email" }, "f1");
    // `Input.insertText` is recorded but the page is never changed, which is
    // what a field that silently drops the text looks like from the executor.
    const { cdp } = fakeCdp();

    const error = await dispatchFill(cdp, "f1", "typed@example.com").catch((thrown) => thrown);

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).disposition).toBe("park");
    expect((error as ExecutorError).message).toContain('"typed@example.com"');
    expect((error as ExecutorError).message).toContain('""');
  });
});

describe("navigate", () => {
  it("a navigate to a refused URL fails", async () => {
    const { cdp } = fakeCdp((method) => {
      if (method === "Page.navigate") return { errorText: "net::ERR_NAME_NOT_RESOLVED" };
      return undefined;
    });

    const error = await dispatchNavigate(cdp, `${ORIGIN}/records/new`).catch(
      (thrown) => thrown,
    );

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).disposition).toBe("park");
    expect((error as ExecutorError).message).toContain("net::ERR_NAME_NOT_RESOLVED");
  });

  it("a navigate waits for the load event", async () => {
    const transport = eventTransport({ loaderId: "L1" });
    let settled = false;
    const navigation = dispatchNavigate(transport.cdp, `${ORIGIN}/records/new`).then(() => {
      settled = true;
    });

    // The browser accepted the navigation but has not loaded the document yet:
    // the step must not be finished.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(settled).toBe(false);
    expect(transport.calls.filter((call) => call.method === "Page.navigate")).toHaveLength(1);

    transport.emit("Page.frameNavigated", { frame: { id: "F1", loaderId: "L1" } });
    transport.emit("Page.loadEventFired", {});
    await navigation;
    expect(settled).toBe(true);

    // A navigation whose load event never arrives times out instead of hanging.
    const silent = eventTransport({ loaderId: "L9" });
    const error = await dispatchNavigate(silent.cdp, `${ORIGIN}/records/new`, 20).catch(
      (thrown) => thrown,
    );
    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).message).toMatch(/did not finish loading/);
  });

  it("a command-mode navigate waits for the timeout on the wire", async () => {
    // The runner states the deadline it will wait for on every command it sends, and the
    // extension must use that value: an extension default longer than the runner's wait is
    // what had a legitimate navigation written off early and paused the run (R4).
    const silent = eventTransport({ loaderId: "L1" });
    const step = {
      id: "open-lead",
      action: "navigate",
      url: `${ORIGIN}/records/new`,
      timeout_ms: 25,
    } as ExecutorStep;

    const error = await performStep(step, { row: {} }, [ORIGIN], {
      cdp: silent.cdp,
      commandMode: true,
    }).catch((thrown: unknown) => thrown);

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).message).toContain("within 25ms");
  });

  it("a load event that arrives before the navigate reply still settles the wait", async () => {
    // B6: the old wait subscribed only after `Page.navigate` replied, so a fast page's load event
    // was dropped and the wait burned its whole timeout on a page that had already loaded.
    const transport = eventTransport({ loaderId: "L2" });
    let settled = false;
    const navigation = dispatchNavigate(transport.cdp, `${ORIGIN}/records/new`).then(() => {
      settled = true;
    });

    // The document navigated and loaded before the reply was even inspected.
    transport.emit("Page.frameNavigated", { frame: { id: "F1", loaderId: "L2" } });
    transport.emit("Page.loadEventFired", {});
    await navigation;
    expect(settled).toBe(true);
  });

  it("a stale load event from the previous document does not settle the wait", async () => {
    // B6: the main frame's id is unchanged across a navigation, so only the loaderId the reply
    // returned names the document this navigation is producing.
    const transport = eventTransport({ loaderId: "L2" });
    let settled = false;
    const navigation = dispatchNavigate(transport.cdp, `${ORIGIN}/records/new`, 30).then(
      () => {
        settled = true;
      },
      () => {
        settled = true;
      },
    );

    // A late load event for the OLD document (loaderId L1) must not count.
    transport.emit("Page.frameNavigated", { frame: { id: "F1", loaderId: "L1" } });
    transport.emit("Page.loadEventFired", {});
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(settled).toBe(false);

    // The real document's own pair settles it.
    transport.emit("Page.frameNavigated", { frame: { id: "F1", loaderId: "L2" } });
    transport.emit("Page.loadEventFired", {});
    await navigation;
    expect(settled).toBe(true);
  });

  it("a same-document navigation resolves without waiting for a load", async () => {
    // B6: no loaderId means no new document, so there is nothing to wait for.
    const transport = eventTransport({ loaderId: "" });
    await dispatchNavigate(transport.cdp, `${ORIGIN}/records#tab`);
    expect(transport.calls.filter((call) => call.method === "Page.navigate")).toHaveLength(1);
  });

  it("a command-mode step with no timeout_ms is refused, not given an extension default", async () => {
    const silent = eventTransport();
    const step = {
      id: "open-lead",
      action: "navigate",
      url: `${ORIGIN}/records/new`,
    } as ExecutorStep;

    const error = await performStep(step, { row: {} }, [ORIGIN], {
      cdp: silent.cdp,
      commandMode: true,
    }).catch((thrown: unknown) => thrown);

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).disposition).toBe("park");
    expect((error as ExecutorError).message).toContain("no timeout_ms");
  });
});

describe("the destructive-control guard", () => {
  it("refuses a click by the resolved accessible name, not the ladder's declaration", async () => {
    // The recipe points at a bare CSS selector, so the ladder declares no name at all. The guard
    // must judge the control a person sees, or a `#danger` button reading "Delete lead" clicks.
    document.body.innerHTML = "<button id='danger'>Delete lead</button>";
    const { cdp } = fakeCdp();
    const step = {
      id: "delete-lead",
      action: "click",
      targets: [{ by: "css", selector: "#danger" }],
    } as unknown as ExecutorStep;

    const error = await performStep(step, { row: {} }, undefined, { cdp }).catch(
      (thrown: unknown) => thrown,
    );

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).disposition).toBe("park");
    expect((error as ExecutorError).message).toContain("delete");
  });

  it("permits it when the step sets allow_destructive_control", async () => {
    document.body.innerHTML = "<button id='danger'>Delete lead</button>";
    const { cdp } = fakeCdp();
    const step = {
      id: "delete-lead",
      action: "click",
      allow_destructive_control: true,
      targets: [{ by: "css", selector: "#danger" }],
    } as unknown as ExecutorStep;

    const report = await performStep(step, { row: {} }, undefined, { cdp });

    expect(report.outcome).toBe("done");
  });

  it("leaves an ordinary click alone", async () => {
    document.body.innerHTML = "<button id='save'>Save</button>";
    const { cdp } = fakeCdp();
    const step = {
      id: "save",
      action: "click",
      targets: [{ by: "css", selector: "#save" }],
    } as unknown as ExecutorStep;

    const report = await performStep(step, { row: {} }, undefined, { cdp });

    expect(report.outcome).toBe("done");
  });
});

describe("select and the fuzzy rung", () => {
  it("a select with no value is refused rather than submitting a blank choice", async () => {
    // F3: a missing input column must park the record, not clear or leave the field.
    document.body.innerHTML =
      "<select id='interest'><option value=''>Choose</option><option value='data'>Data</option></select>";
    const { cdp, callsTo } = fakeCdp();
    const step = {
      id: "select-1",
      action: "select",
      targets: [{ by: "css", selector: "#interest" }],
      value: { kind: "constant", value: "" },
    } as unknown as ExecutorStep;

    const error = await performStep(step, { row: {} }, undefined, { cdp }).catch(
      (thrown: unknown) => thrown,
    );

    expect(error).toBeInstanceOf(ExecutorError);
    expect((error as ExecutorError).disposition).toBe("park");
    expect((error as ExecutorError).message).toMatch(/no value to choose/);
    expect(callsTo("Runtime.evaluate")).toHaveLength(0);
  });

  it("a combobox option is found only inside its listbox", async () => {
    document.body.innerHTML =
      "<div id='decoy' role='listbox'><div role='option'>Data course</div></div>" +
      "<div id='cb' role='combobox' aria-controls='dd-list' aria-expanded='true'>Interest</div>" +
      "<div id='dd-list' role='listbox'><div id='real-option' role='option'>Data course</div></div>";
    const { cdp } = fakeCdp();
    const step = {
      id: "select-1",
      action: "select",
      targets: [{ by: "css", selector: "#cb" }],
      value: { kind: "constant", value: "Data course" },
    } as unknown as ExecutorStep;

    const report = await performStep(step, { row: {} }, undefined, { cdp });

    expect(report.outcome).toBe("done");
    // The option that was clicked is the one inside the listbox the combobox
    // names, not the identically named option of the other listbox.
    expect(anyMarked()?.id).toBe("real-option");
    expect(anyMarked()?.closest("[role='listbox']")?.id).toBe("dd-list");
  });

  it("the fuzzy rung ignores typed text", () => {
    // The input currently *holds* "Ravi"; that is not its name, so rung 5 must
    // not match on it and must find the button that is labelled "Ravi".
    stamp(
      "<input id='email' value='Ravi'><button id='save'>Ravi</button>",
      { by: "text_fuzzy", value: "Ravi" },
      "t1",
    );
    const result = resolveTargetInPage({
      targets: [{ by: "text_fuzzy", value: "Ravi" }],
      within: null,
      token: "t2",
    });

    expect(result.ok).toBe(true);
    expect(result.rung).toBe(5);
    expect(document.querySelector('[data-pl-target="t2"]')?.id).toBe("save");

    // A label it does read still matches: the placeholder.
    document.body.innerHTML = "<input id='search' placeholder='Search records'>";
    const byPlaceholder = resolveTargetInPage({
      targets: [{ by: "text_fuzzy", value: "Search records" }],
      within: null,
      token: "t3",
    });
    expect(byPlaceholder.ok).toBe(true);
    expect(document.querySelector('[data-pl-target="t3"]')?.id).toBe("search");
  });
});

describe("abort", () => {
  it("an abort ends a condition poll", async () => {
    const { cdp, callsTo } = fakeCdp();
    let aborted = false;
    let sleeps = 0;

    const outcome = await waitForCondition(
      cdp,
      { text_visible: "Record created" },
      { resolverSource: "() => ({ ok: false })" },
      {
        timeoutMs: 300,
        pollMs: 1,
        aborted: () => aborted,
        sleep: async () => {
          sleeps += 1;
          aborted = true;
        },
      },
    );

    // The poll stops at the next iteration instead of waiting out its timeout.
    expect(outcome.ok).toBe(false);
    expect(outcome.aborted).toBe(true);
    expect(outcome.reason).toMatch(/aborted/);
    expect(outcome.polls).toBe(2);
    expect(sleeps).toBe(1);
    expect(callsTo("Runtime.evaluate")).toHaveLength(1);

    // And the executor hands that probe to the poll: a run told to abort while a
    // post-condition is being checked ends as an abort.
    stamp("<button id='save'>Save</button>", { by: "css", selector: "#save" }, "c1");
    const control = new RunControl();
    const transport = fakeCdp((method) => {
      if (method === "Input.dispatchMouseEvent") control.abort("the debugger detached");
      return undefined;
    });
    const summary = await runRecipe(
      {
        format_version: 1,
        meta: { name: "poll", allowed_origins: [ORIGIN] },
        steps: [
          {
            id: "click-1",
            action: "click",
            assert_after: { text_visible: "Never appears", timeout_ms: 400 },
            targets: [{ by: "css", selector: "#save" }],
          },
        ],
      },
      { row: {} },
      { cdp: transport.cdp, control },
    );

    expect(summary.state).toBe("aborted");
    expect(summary.error).toMatch(/debugger detached/);
  });
});
