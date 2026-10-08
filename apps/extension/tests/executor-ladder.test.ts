// The 5-rung target ladder, exercised against a real jsdom document.
//
// The resolver runs in the page, so these tests mount real markup and call it
// directly — the same function the executor serializes into a page.
import { beforeEach, describe, expect, it } from "vitest";
import { TARGET_MARKER, resolveTargetInPage, type ResolveQuery, type ResolveRung } from "../src/executor/ladder";

function query(targets: ResolveRung[], within: string | null = null, token = "t1"): ResolveQuery {
  return { targets, within, token };
}

function marked(token = "t1"): Element | null {
  return document.querySelector(`[${TARGET_MARKER}="${token}"]`);
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("rung 1: role and accessible name", () => {
  it("finds a button by its implicit role and visible name", () => {
    document.body.innerHTML = "<button id='save'>Save</button>";
    const result = resolveTargetInPage(query([{ by: "role", role: "button", name: "Save" }]));
    expect(result.ok).toBe(true);
    expect(result.rung).toBe(0);
    expect(result.by).toBe("role");
    expect(marked()?.id).toBe("save");
  });

  it("reads an explicit role", () => {
    document.body.innerHTML = "<div id='tab' role='tab'>Overview</div>";
    const result = resolveTargetInPage(query([{ by: "role", role: "tab", name: "Overview" }]));
    expect(result.ok).toBe(true);
    expect(marked()?.id).toBe("tab");
  });

  it("scopes to a named form container", () => {
    document.body.innerHTML =
      "<form aria-label='New lead'><button id='save'>Save</button></form>" +
      "<button id='other'>Save</button>";
    const result = resolveTargetInPage(
      query([{ by: "role", role: "button", name: "Save", within: "form:New lead" }]),
    );
    expect(result.ok).toBe(true);
    expect(marked()?.id).toBe("save");
  });

  it("records an ambiguous rung and climbs to the next one", () => {
    document.body.innerHTML =
      "<button class='save'>Save</button><button class='save'>Save</button>" +
      "<button id='explicit' data-testid='lead-save' aria-label='Save lead'>Save</button>";
    const result = resolveTargetInPage(
      query([
        { by: "role", role: "button", name: "Save" },
        { by: "testid", value: "lead-save" },
      ]),
    );
    expect(result.ok).toBe(true);
    expect(result.rung).toBe(1);
    expect(result.attempts[0]).toMatchObject({ rung: 0, matches: 2, reason: "more than one match" });
    expect(marked()?.id).toBe("explicit");
  });

  it("fails when no rung resolves to exactly one element", () => {
    document.body.innerHTML = "<button>Save</button>";
    const result = resolveTargetInPage(query([{ by: "role", role: "link", name: "Save" }]));
    expect(result.ok).toBe(false);
    expect(result.marker).toBeNull();
    expect(result.attempts).toHaveLength(1);
  });
});

describe("rungs 2 to 5", () => {
  it("rung 2 finds a data-testid", () => {
    document.body.innerHTML = "<input id='email' data-testid='lead-email'>";
    const result = resolveTargetInPage(query([{ by: "testid", value: "lead-email" }]));
    expect(result.ok).toBe(true);
    expect(marked()?.id).toBe("email");
  });

  it("rung 2 also finds data-test", () => {
    document.body.innerHTML = "<input id='email' data-test='lead-email'>";
    const result = resolveTargetInPage(query([{ by: "testid", value: "lead-email" }]));
    expect(result.ok).toBe(true);
    expect(marked()?.id).toBe("email");
  });

  it("rung 3 finds a control through its label", () => {
    document.body.innerHTML = "<label for='email'>Email</label><input id='email'>";
    const result = resolveTargetInPage(query([{ by: "label", text: "Email" }]));
    expect(result.ok).toBe(true);
    expect(marked()?.id).toBe("email");
  });

  it("rung 3 finds a control inside a wrapping label", () => {
    document.body.innerHTML = "<label>Phone <input id='phone'></label>";
    const result = resolveTargetInPage(query([{ by: "label", text: "Phone" }]));
    expect(result.ok).toBe(true);
    expect(marked()?.id).toBe("phone");
  });

  it("rung 4 finds a stable CSS selector", () => {
    document.body.innerHTML = "<button id='lead-save'>Save</button>";
    const result = resolveTargetInPage(query([{ by: "css", selector: "#lead-save" }]));
    expect(result.ok).toBe(true);
    expect(result.by).toBe("css");
  });

  it("rung 4 reports an invalid selector and moves on", () => {
    document.body.innerHTML = "<button id='save'>Save</button>";
    const result = resolveTargetInPage(
      query([
        { by: "css", selector: ">>>nonsense" },
        { by: "css", selector: "#save" },
      ]),
    );
    expect(result.ok).toBe(true);
    expect(result.rung).toBe(1);
    expect(result.attempts[0]?.reason).toContain("invalid selector");
  });

  it("rung 5 matches by fuzzy text, preferring the deepest element", () => {
    document.body.innerHTML = "<div id='wrap'><span id='inner'>Save lead</span></div>";
    const result = resolveTargetInPage(query([{ by: "text_fuzzy", value: "Save lead" }]));
    expect(result.ok).toBe(true);
    expect(marked()?.id).toBe("inner");
  });
});

describe("usability and scoping", () => {
  it("ignores a hidden element", () => {
    document.body.innerHTML = "<button id='save' hidden>Save</button>";
    const result = resolveTargetInPage(query([{ by: "role", role: "button", name: "Save" }]));
    expect(result.ok).toBe(false);
  });

  it("ignores a disabled element", () => {
    document.body.innerHTML = "<button id='save' disabled>Save</button>";
    const result = resolveTargetInPage(query([{ by: "role", role: "button", name: "Save" }]));
    expect(result.ok).toBe(false);
  });

  it("reports a missing container scope", () => {
    document.body.innerHTML = "<button id='save'>Save</button>";
    const result = resolveTargetInPage(
      query([{ by: "role", role: "button", name: "Save" }], "form:Missing"),
    );
    expect(result.ok).toBe(false);
    expect(result.attempts[0]?.reason).toContain("container");
  });

  it("stamps the marker on the element it found", () => {
    document.body.innerHTML = "<button id='save'>Save</button>";
    resolveTargetInPage(query([{ by: "role", role: "button", name: "Save" }], null, "run-7"));
    expect(marked("run-7")?.id).toBe("save");
  });

  it("clears an older marker when it finds a new target", () => {
    document.body.innerHTML = "<button id='save'>Save</button><button id='cancel'>Cancel</button>";
    resolveTargetInPage(query([{ by: "role", role: "button", name: "Save" }], null, "a"));
    resolveTargetInPage(query([{ by: "role", role: "button", name: "Cancel" }], null, "b"));
    expect(marked("a")).toBeNull();
    expect(marked("b")?.id).toBe("cancel");
  });

  it("fails when the step has no ladder", () => {
    const result = resolveTargetInPage(query([]));
    expect(result.ok).toBe(false);
    expect(result.error).toContain("no target ladder");
  });

  it("returns the box center for a found element", () => {
    document.body.innerHTML = "<button id='save'>Save</button>";
    const result = resolveTargetInPage(query([{ by: "role", role: "button", name: "Save" }]));
    expect(typeof result.x).toBe("number");
    expect(typeof result.y).toBe("number");
  });
});
