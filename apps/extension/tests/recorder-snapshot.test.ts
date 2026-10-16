// The state-diff engine, end to end through the recorder: what a checkpoint sees that no event
// reported, and what it must never see (a password, or a suggestion popup read as a chip list).
//
// jsdom has no layout, so every element would read as invisible. The tests declare the page
// visible, which is what makes the visibility rule a decision here rather than an accident.
import { beforeEach, describe, expect, it } from "vitest";
import {
  PAGE_SET_VALUE_WARNING,
  UNRECORDABLE_CHIP_WARNING,
  attachRecorder,
} from "../src/content/recorder";
import { collectSnapshot } from "../src/content/snapshot";
import { resetStepIds, type PendingEvent } from "../src/shared/events";

function declareVisibleIn(win: Window): void {
  const proto = (win as Window & { Element?: { prototype: object } }).Element?.prototype;
  if (proto) {
    Object.defineProperty(proto, "checkVisibility", {
      configurable: true,
      value: () => true,
    });
  }
}

function declareVisible(): void {
  declareVisibleIn(window);
  // Each iframe is its own realm in jsdom, with its own `Element.prototype`; a same-origin
  // frame's fields are only visible once its realm says so too.
  for (const frame of Array.from(document.querySelectorAll("iframe"))) {
    const inner = (frame as HTMLIFrameElement).contentDocument;
    if (inner?.defaultView) declareVisibleIn(inner.defaultView);
  }
}

interface Harness {
  events: PendingEvent[];
  checkpoint: () => void;
  detach: () => void;
}

function harness(): Harness {
  const events: PendingEvent[] = [];
  const handle = attachRecorder(document, {
    send: (event) => events.push(event),
    isActive: () => true,
    url: () => "https://mail.example.test/inbox",
    now: () => "2026-10-14T00:00:00.000Z",
  });
  return { events, checkpoint: handle.checkpoint, detach: handle };
}

function kinds(events: PendingEvent[]): string[] {
  return events.map((event) => event.kind);
}

function fillValues(events: PendingEvent[]): unknown[] {
  return events
    .filter((event) => event.kind === "fill")
    .map((event) => (event.step && "value" in event.step ? event.step.value : null));
}

function click(id: string): void {
  document.getElementById(id)!.dispatchEvent(new Event("click", { bubbles: true }));
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetStepIds();
  declareVisible();
});

describe("the baseline and the diff", () => {
  it("takes the page as it was found: a prefilled form produces no steps", () => {
    document.body.innerHTML =
      "<label for='email'>Email</label><input id='email' value='ravi@example.test'>" +
      "<button id='save'>Save</button>";
    const recorded = harness();

    recorded.checkpoint(); // the baseline
    click("save");

    expect(kinds(recorded.events)).toEqual(["click"]);
  });

  it("emits the typing before the click that triggered the checkpoint", () => {
    document.body.innerHTML =
      "<label for='email'>Email</label><input id='email'><button id='accept'>Accept</button>";
    const recorded = harness();
    recorded.checkpoint();

    const field = document.getElementById("email") as HTMLInputElement;
    field.value = "ma";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    click("accept");

    expect(kinds(recorded.events)).toEqual(["fill", "click"]);
    expect(fillValues(recorded.events)).toEqual([{ kind: "constant", value: "ma" }]);
  });

  it("emits only the click when a value was typed and then deleted (change and change back)", () => {
    document.body.innerHTML =
      "<label for='email'>Email</label><input id='email'><button id='send'>Send</button>";
    const recorded = harness();
    recorded.checkpoint();

    const field = document.getElementById("email") as HTMLInputElement;
    field.value = "ma";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.value = "";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    click("send");

    expect(kinds(recorded.events)).toEqual(["click"]);
  });

  it("catches a contenteditable the page mutated, with no input event, and says so", () => {
    document.body.innerHTML =
      "<div id='body' contenteditable='true' aria-label='Message'></div><button id='send'>Send</button>";
    const recorded = harness();
    recorded.checkpoint();

    document.getElementById("body")!.textContent = "hello";
    click("send");

    expect(kinds(recorded.events)).toEqual(["fill", "click"]);
    expect(fillValues(recorded.events)).toEqual([{ kind: "constant", value: "hello" }]);
    expect(recorded.events[0]!.warning).toContain(PAGE_SET_VALUE_WARNING);
    // PL-027: the fact rides the envelope as a boolean too. The compiler drops a page-set fill by
    // reading it, instead of matching the English sentence above -- Odoo renders computed defaults
    // (a zero, a resolved many2one) into its forms, and replaying them would type the page's own
    // arithmetic back into a field.
    expect(recorded.events[0]!.page_set).toBe(true);
  });

  it("reads an open shadow root and a same-origin frame", () => {
    document.body.innerHTML =
      "<div id='host'></div><iframe id='frame'></iframe><button id='go'>Go</button>";
    const host = document.getElementById("host")!;
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = "<label for='s'>Shadow</label><input id='s'>";
    const frame = document.getElementById("frame") as HTMLIFrameElement;
    const inner = frame.contentDocument!;
    inner.body.innerHTML = "<label for='f'>Framed</label><input id='f'>";
    // The frame's realm was created with the iframe; declare its visibility regime now.
    declareVisible();

    const recorded = harness();
    recorded.checkpoint();

    const shadowField = root.getElementById("s") as HTMLInputElement;
    shadowField.value = "from shadow";
    const framedField = inner.getElementById("f") as HTMLInputElement;
    framedField.value = "from frame";
    click("go");

    expect(fillValues(recorded.events)).toEqual([
      { kind: "constant", value: "from shadow" },
      { kind: "constant", value: "from frame" },
    ]);
  });

  it("attributes typing inside an open shadow root to the inner field, not its host", () => {
    document.body.innerHTML = "<div id='host'></div><button id='go'>Go</button>";
    const host = document.getElementById("host")!;
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = "<label for='s'>Name</label><input id='s' aria-label='Name'>";
    declareVisible();
    const recorded = harness();
    recorded.checkpoint();

    // A key event inside a shadow root is retargeted to the host at the document; the recorder
    // must read the composed path's first node or it credits the host with the person's typing.
    const field = root.getElementById("s") as HTMLInputElement;
    field.value = "from shadow";
    field.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    click("go");

    const fills = recorded.events.filter((event) => event.kind === "fill");
    expect(fills).toHaveLength(1);
    expect(fills[0]!.warning).toBeUndefined();
    expect(fills[0]!.step).toMatchObject({
      targets: expect.arrayContaining([{ by: "css", selector: "#s" }]),
      value: { kind: "constant", value: "from shadow" },
    });
    // And the focus click on the host is not recorded as a step of its own.
    expect(recorded.events.filter((event) => event.kind === "click")).toHaveLength(1);
  });

  it("never reads a password field's value, even when the page fills it", () => {
    document.body.innerHTML =
      "<input id='user' aria-label='User'>" +
      "<input id='pw' type='password' aria-label='Password'>" +
      "<button id='go'>Go</button>";
    const recorded = harness();
    recorded.checkpoint();

    const password = document.getElementById("pw") as HTMLInputElement;
    password.value = "correct-horse-battery-staple";
    (document.getElementById("user") as HTMLInputElement).value = "ravi@example.test";
    password.dispatchEvent(new Event("input", { bubbles: true }));
    click("go");

    expect(JSON.stringify(recorded.events)).not.toContain("correct-horse");
    expect(fillValues(recorded.events)).toEqual([{ kind: "constant", value: "ravi@example.test" }]);
  });
});

describe("chip lists", () => {
  function chipsPage(): void {
    document.body.innerHTML = `
      <div>
        <input id="to" role="combobox" aria-label="To" aria-controls="suggestions" aria-expanded="true">
        <ul id="suggestions" role="listbox" aria-label="Suggestions">
          <li role="option">Mary An ma@example.test</li>
        </ul>
        <ul id="recipients" aria-label="Recipients">
          <li>Ada <button aria-label="Remove ada@example.test">x</button></li>
        </ul>
      </div>`;
  }

  it("reads a labelled chip container, but never a combobox's suggestion popup", () => {
    chipsPage();
    const recorded = harness();
    recorded.checkpoint();

    document
      .getElementById("suggestions")!
      .insertAdjacentHTML("beforeend", "<li role='option'>Mary Anne mary@example.test</li>");
    recorded.checkpoint();

    expect(recorded.events.filter((event) => event.chip === true)).toHaveLength(0);

    document
      .getElementById("recipients")!
      .insertAdjacentHTML("beforeend", "<li>Bob <button aria-label='Remove bob@example.test'>x</button></li>");
    recorded.checkpoint();

    const chips = recorded.events.filter((event) => event.chip === true);
    expect(chips).toHaveLength(1);
    expect(chips[0]!.step).toMatchObject({
      action: "fill",
      clear_first: false,
      value: { kind: "constant", value: "bob@example.test" },
    });
  });

  it("takes a chip's address, not its display name", () => {
    document.body.innerHTML =
      "<ul aria-label='Recipients'><li data-address='carol@example.test'>Carol Diaz " +
      "<button aria-label='Remove carol@example.test'>x</button></li></ul>";
    const recorded = harness();
    recorded.checkpoint();

    document
      .querySelector("ul")!
      .insertAdjacentHTML("beforeend", "<li data-address='dan@example.test'>Dan Rivera</li>");
    recorded.checkpoint();

    expect(fillValues(recorded.events)).toEqual([{ kind: "constant", value: "dan@example.test" }]);
  });

  it("marks the chip fill and names the prefix fill it supersedes", () => {
    chipsPage();
    const recorded = harness();
    recorded.checkpoint();

    const field = document.getElementById("to") as HTMLInputElement;
    field.value = "ma";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    // The widget accepted on the press: the field was cleared and the chip added in one tick.
    document
      .getElementById("recipients")!
      .insertAdjacentHTML("beforeend", "<li>Mary An <button aria-label='Remove ma@example.test'>x</button></li>");
    field.value = "";
    recorded.checkpoint();

    const fills = recorded.events.filter((event) => event.kind === "fill");
    expect(fills).toHaveLength(2);
    expect(fills[0]!.step).toMatchObject({ value: { kind: "constant", value: "ma" } });

    const chip = fills[1]!;
    expect(chip.chip).toBe(true);
    expect(chip.autocomplete).toBe(true);
    expect(chip.supersedes).toBe(fills[0]!.step?.id);
    expect(chip.step).toMatchObject({
      value: { kind: "constant", value: "ma@example.test" },
      clear_first: false,
    });
  });

  it("records the remove click as the step, and reports a chip that vanished with none", () => {
    document.body.innerHTML =
      "<ul id='list' aria-label='Recipients'>" +
      "<li>Ada <button id='remove-ada' aria-label='Remove ada@example.test'>x</button></li>" +
      "<li data-address='bob@example.test'>Bob</li>" +
      "</ul>";
    const recorded = harness();
    recorded.checkpoint();

    // A control whose name says Remove: the click is the step for the chip leaving.
    document.getElementById("remove-ada")!.dispatchEvent(new Event("click", { bubbles: true }));
    const first = document.getElementById("list")!.querySelector("li")!;
    first.remove();
    recorded.checkpoint();

    expect(kinds(recorded.events)).toEqual(["click"]);

    // A chip that vanished with no remove control clicked is not replayable, and says so.
    document.querySelector("[data-address='bob@example.test']")!.remove();
    recorded.checkpoint();

    const removal = recorded.events.filter((event) => event.kind === "unrecordable");
    expect(removal).toHaveLength(1);
    expect(removal[0]).toMatchObject({ step: null, reason: "chip-removal" });
    expect(removal[0]!.warning).toContain(UNRECORDABLE_CHIP_WARNING);
  });
});

describe("the snapshot's cost", () => {
  it("snapshots a visible 1000-field page and prints how long it took", () => {
    const fields = Array.from(
      { length: 1000 },
      (_unused, index) => `<label for='f${index}'>Field ${index}</label><input id='f${index}' value='v${index}'>`,
    );
    document.body.innerHTML = fields.join("");

    const started = Date.now();
    const snapshot = collectSnapshot(document);
    const elapsed = Date.now() - started;
    // The plan's budget is < 16 ms on the test machine; the number is reported either way.
    console.log(`[PL-018] snapshot of 1000 fields: ${elapsed} ms`);

    expect(snapshot.truncated).toBe(false);
    expect(snapshot.entries.size).toBe(1000);

    const recorded = harness();
    recorded.checkpoint(); // the baseline over 1000 fields emits nothing
    expect(recorded.events).toHaveLength(0);
  });
});
