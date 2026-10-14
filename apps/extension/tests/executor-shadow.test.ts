// Resolving a control that lives inside an OPEN shadow root.
//
// The corpus's `shadow-form.html` widget mounts its form inside `attachShadow({ mode: "open" })`.
// Every page-side lookup used to be a `document.querySelector…`, and a document query stops at a
// shadow boundary — so the ladder could never see the widget's controls, and never could the marker
// lookup that finds them again. These tests mount real shadow trees in jsdom and call the same
// resolver the executor serializes into a page.
import { beforeEach, describe, expect, it } from "vitest";
import {
  TARGET_MARKER,
  resolveTargetInPage,
  resolverExpression,
  type ResolveQuery,
  type ResolveRung,
} from "../src/executor/ladder";
import { markerExpression, resolverCallExpression } from "../src/executor/page";
import { fakeCdp } from "./helpers/fake-cdp";

function query(targets: ResolveRung[], within: string | null = null, token = "t1"): ResolveQuery {
  return { targets, within, token };
}

/** The marker as a document-level lookup finds it: light DOM only. */
function marked(token = "t1"): Element | null {
  return document.querySelector(`[${TARGET_MARKER}="${token}"]`);
}

/** The marker inside one shadow root. */
function markedIn(root: ShadowRoot, token = "t1"): Element | null {
  return root.querySelector(`[${TARGET_MARKER}="${token}"]`);
}

/** Mount the shadow widget: a form, its labels, and its controls, all inside an open shadow root. */
function mountShadowForm(hostId = "lead-form"): ShadowRoot {
  document.body.innerHTML = `<div id="${hostId}"></div>`;
  const host = document.getElementById(hostId) as HTMLElement;
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML =
    "<form>" +
    "<label for='shadow-name'>Name</label>" +
    "<input id='shadow-name' type='text'>" +
    "<label for='shadow-notes'>Notes</label>" +
    "<textarea id='shadow-notes'></textarea>" +
    "<button id='shadow-save' type='button'>Save</button>" +
    "</form>";
  return root;
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("the ladder inside an open shadow root", () => {
  it("a role rung resolves a shadow input by its accessible name", () => {
    const root = mountShadowForm();

    const result = resolveTargetInPage(query([{ by: "role", role: "textbox", name: "Name" }]));

    expect(result.ok).toBe(true);
    expect(result.rung).toBe(1);
    expect(result.by).toBe("role");
    expect(result.tag).toBe("input");
    // The name comes from the shadow tree's own `label[for]`, which a document query cannot reach.
    expect(result.name).toBe("Name");
    // The marker is on the shadow control — where the old document-level lookup never looked.
    expect(markedIn(root)?.id).toBe("shadow-name");
    expect(marked()).toBeNull();
  });

  it("a testid rung resolves a shadow control", () => {
    document.body.innerHTML = "<div id='host'></div>";
    const host = document.getElementById("host") as HTMLElement;
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = "<input id='shadow-email' data-testid='lead-email'>";

    const result = resolveTargetInPage(query([{ by: "testid", value: "lead-email" }]));

    expect(result.ok).toBe(true);
    expect(result.rung).toBe(2);
    expect(markedIn(root)?.id).toBe("shadow-email");
  });

  it("a label rung resolves a labelled control inside a shadow root", () => {
    const root = mountShadowForm();

    const result = resolveTargetInPage(query([{ by: "label", text: "Notes" }]));

    expect(result.ok).toBe(true);
    expect(result.rung).toBe(3);
    expect(result.by).toBe("label");
    expect(markedIn(root)?.id).toBe("shadow-notes");
  });

  it("a css rung resolves an id inside a shadow root", () => {
    const root = mountShadowForm();

    const result = resolveTargetInPage(query([{ by: "css", selector: "#shadow-name" }]));

    expect(result.ok).toBe(true);
    expect(result.rung).toBe(4);
    expect(result.by).toBe("css");
    expect(markedIn(root)?.id).toBe("shadow-name");
    expect(marked()).toBeNull();
  });

  it("a closed shadow root is not seen at all", () => {
    document.body.innerHTML = "<div id='closed-host'></div>";
    const host = document.getElementById("closed-host") as HTMLElement;
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = "<label for='secret-name'>Name</label><input id='secret-name' type='text'>";

    const result = resolveTargetInPage(
      query([
        { by: "role", role: "textbox", name: "Name" },
        { by: "css", selector: "#secret-name" },
      ]),
    );

    // Closed content is invisible to page script, so the resolver refuses the target — a
    // documented no-match, not a crash and not a guess.
    expect(result.ok).toBe(false);
    expect(result.marker).toBeNull();
    expect(result.error).toMatch(/no rung/);
    expect(result.attempts).toHaveLength(2);
    expect(marked()).toBeNull();
    expect(root.querySelector(`[${TARGET_MARKER}]`)).toBeNull();
  });

  it("a light-DOM control still resolves with the same rung", () => {
    document.body.innerHTML = "<label for='email'>Email</label><input id='email'>";

    const result = resolveTargetInPage(query([{ by: "role", role: "textbox", name: "Email" }]));

    expect(result.ok).toBe(true);
    expect(result.rung).toBe(1);
    expect(result.by).toBe("role");
    expect(marked()?.id).toBe("email");
  });

  it("a light-DOM control still resolves beside a shadow widget", () => {
    const root = mountShadowForm();
    const light = document.createElement("input");
    light.id = "light-name";
    light.setAttribute("aria-label", "Light name");
    document.body.appendChild(light);

    const result = resolveTargetInPage(
      query([{ by: "role", role: "textbox", name: "Light name" }]),
    );

    expect(result.ok).toBe(true);
    expect(result.rung).toBe(1);
    expect(marked()?.id).toBe("light-name");
    expect(markedIn(root)).toBeNull();
  });
});

describe("the marker lookup inside an open shadow root", () => {
  it("the injected resolver source resolves a shadow control", async () => {
    // The executor never calls the resolver directly: it injects its source. The helpers above must
    // therefore be self-contained, or the injected expression would reference a name that does not
    // exist in the page.
    const root = mountShadowForm();
    const { cdp } = fakeCdp();
    const reply = (await cdp.send("Runtime.evaluate", {
      expression: resolverCallExpression(resolverExpression(), {
        targets: [{ by: "css", selector: "#shadow-name" }],
        within: null,
        token: "inj-1",
      }),
      returnByValue: true,
      awaitPromise: true,
    })) as { result?: { value?: unknown } };

    expect(reply.result?.value).toMatchObject({ ok: true, rung: 4, by: "css" });
    expect(markedIn(root, "inj-1")?.id).toBe("shadow-name");
  });

  it("markerExpression finds an element stamped inside a shadow root", async () => {
    const root = mountShadowForm();
    resolveTargetInPage(query([{ by: "css", selector: "#shadow-name" }], null, "run-9"));
    expect(marked("run-9")).toBeNull(); // the marker is not reachable from the light DOM …

    const { cdp } = fakeCdp();
    const reply = (await cdp.send("Runtime.evaluate", {
      expression: markerExpression("run-9"),
      returnByValue: true,
    })) as { result?: { value?: unknown } };

    // … and the expression the executor actually injects finds it all the same.
    expect(reply.result?.value).toBe(markedIn(root, "run-9"));
    expect((reply.result?.value as Element | undefined)?.id).toBe("shadow-name");
  });

  it("stamping a new target clears a stale marker inside a shadow root", () => {
    const root = mountShadowForm();
    resolveTargetInPage(query([{ by: "css", selector: "#shadow-name" }], null, "old"));
    expect(markedIn(root, "old")?.id).toBe("shadow-name");

    const light = document.createElement("button");
    light.id = "save";
    light.textContent = "Save";
    document.body.appendChild(light);
    resolveTargetInPage(query([{ by: "css", selector: "#save" }], null, "fresh"));

    expect(markedIn(root, "old")).toBeNull();
    expect(marked("fresh")?.id).toBe("save");
  });

  it("stamping a second shadow target clears the first", () => {
    const root = mountShadowForm();
    resolveTargetInPage(query([{ by: "css", selector: "#shadow-name" }], null, "one"));
    resolveTargetInPage(query([{ by: "css", selector: "#shadow-save" }], null, "two"));

    expect(markedIn(root, "one")).toBeNull();
    expect(markedIn(root, "two")?.id).toBe("shadow-save");
  });
});
