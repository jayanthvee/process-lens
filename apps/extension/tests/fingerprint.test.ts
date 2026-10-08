import { describe, expect, it } from "vitest";
import {
  accessibleName,
  computeTargets,
  enclosingContainer,
  isPasswordField,
  isStableToken,
  labelText,
  semanticRole,
  stableSelector,
} from "../src/shared/fingerprint";
import type { LocatorRung } from "../src/shared/recipe-types";

function rungBy(targets: LocatorRung[], by: LocatorRung["by"]): LocatorRung | undefined {
  return targets.find((rung) => rung.by === by);
}

function mount(html: string): HTMLElement {
  document.body.innerHTML = html;
  return document.body;
}

describe("semantic role", () => {
  it("reads the implicit role of common controls", () => {
    mount(
      "<button id='b'>Save</button><select id='s'></select><textarea id='t'></textarea><a id='a' href='/x'>Home</a>",
    );
    expect(semanticRole(document.getElementById("b")!)).toBe("button");
    expect(semanticRole(document.getElementById("s")!)).toBe("combobox");
    expect(semanticRole(document.getElementById("t")!)).toBe("textbox");
    expect(semanticRole(document.getElementById("a")!)).toBe("link");
  });

  it("reads input types", () => {
    mount("<input id='c' type='checkbox'><input id='e' type='email'>");
    expect(semanticRole(document.getElementById("c")!)).toBe("checkbox");
    expect(semanticRole(document.getElementById("e")!)).toBe("textbox");
  });

  it("prefers an explicit role", () => {
    mount("<div id='d' role='dialog'>Hi</div>");
    expect(semanticRole(document.getElementById("d")!)).toBe("dialog");
  });

  it("treats a link without href as generic", () => {
    mount("<a id='a'>not a link</a>");
    expect(semanticRole(document.getElementById("a")!)).toBe("generic");
  });
});

describe("accessible name", () => {
  it("uses the visible text of a button", () => {
    mount("<button id='b'>Save lead</button>");
    expect(accessibleName(document.getElementById("b")!)).toBe("Save lead");
  });

  it("uses aria-label before the text", () => {
    mount("<button id='b' aria-label='Close dialog'>&times;</button>");
    expect(accessibleName(document.getElementById("b")!)).toBe("Close dialog");
  });

  it("uses aria-labelledby", () => {
    mount("<span id='l'>Assigned to</span><input id='i' aria-labelledby='l'>");
    expect(accessibleName(document.getElementById("i")!)).toBe("Assigned to");
  });

  it("uses a label associated by for", () => {
    mount("<label for='e'>Email</label><input id='e'>");
    expect(accessibleName(document.getElementById("e")!)).toBe("Email");
  });

  it("uses a wrapping label", () => {
    mount("<label>Phone <input id='p'></label>");
    expect(accessibleName(document.getElementById("p")!)).toBe("Phone");
  });

  it("falls back to the placeholder for an unlabelled input", () => {
    mount("<input id='s' placeholder='Search customers'>");
    expect(accessibleName(document.getElementById("s")!)).toBe("Search customers");
  });

  it("uses the value of a submit input", () => {
    mount("<input id='s' type='submit' value='Create'>");
    expect(accessibleName(document.getElementById("s")!)).toBe("Create");
  });
});

describe("labels", () => {
  it("finds the label text for a field", () => {
    mount("<label for='e'>Email address</label><input id='e'>");
    expect(labelText(document.getElementById("e")!)).toBe("Email address");
  });

  it("returns empty for an element with no label", () => {
    mount("<button id='b'>Save</button>");
    expect(labelText(document.getElementById("b")!)).toBe("");
  });
});

describe("enclosing container", () => {
  it("scopes to a named form", () => {
    mount("<form aria-label='New lead'><button id='b'>Save</button></form>");
    expect(enclosingContainer(document.getElementById("b")!)).toBe("form:New lead");
  });

  it("scopes to a named dialog by role", () => {
    mount("<div role='dialog' aria-label='New lead'><button id='b'>Save</button></div>");
    expect(enclosingContainer(document.getElementById("b")!)).toBe("dialog:New lead");
  });

  it("ignores an unnamed wrapper", () => {
    mount("<div><button id='b'>Save</button></div>");
    expect(enclosingContainer(document.getElementById("b")!)).toBeUndefined();
  });

  it("uses a legend for a fieldset", () => {
    mount("<fieldset><legend>Shipping</legend><input id='i'></fieldset>");
    expect(enclosingContainer(document.getElementById("i")!)).toBe("fieldset:Shipping");
  });
});

describe("stable tokens", () => {
  it("accepts human-written names", () => {
    for (const token of ["lead-save", "customerSearch", "save", "new-lead-form"]) {
      expect(isStableToken(token)).toBe(true);
    }
  });

  it("rejects generated hashes", () => {
    for (const token of ["css-1q2w3e", "sc-bdfBQB", "a1b2c3d4e5", "1234", "makeStyles-root-12"]) {
      expect(isStableToken(token)).toBe(false);
    }
  });
});

describe("stable selector", () => {
  it("prefers a stable id", () => {
    mount("<button id='lead-save'>Save</button>");
    expect(stableSelector(document.getElementById("lead-save")!)).toBe("#lead-save");
  });

  it("uses stable classes and ignores hashed ones", () => {
    mount("<button class='css-1q2w3e lead-save'>Save</button>");
    expect(stableSelector(document.querySelector("button")!)).toBe("button.lead-save");
  });

  it("falls back to a positional path", () => {
    mount("<form><button>Save</button></form>");
    expect(stableSelector(document.querySelector("button")!)).toBe("form > button");
  });
});

describe("target ladder", () => {
  it("orders the rungs role, test id, label, css, fuzzy text", () => {
    mount(
      "<form aria-label='New lead'><label for='e'>Email</label><input id='e' data-testid='lead-email' placeholder='Email'></form>",
    );
    const targets = computeTargets(document.getElementById("e")!);
    expect(targets.map((rung) => rung.by)).toEqual(["role", "testid", "label", "css", "text_fuzzy"]);
    expect(rungBy(targets, "role")).toMatchObject({
      role: "textbox",
      name: "Email",
      within: "form:New lead",
    });
    expect(rungBy(targets, "testid")).toMatchObject({ value: "lead-email" });
    expect(rungBy(targets, "label")).toMatchObject({ text: "Email" });
  });

  it("never returns more than five rungs", () => {
    mount("<button id='b' data-testid='x' aria-label='Save'>Save</button>");
    expect(computeTargets(document.getElementById("b")!).length).toBeLessThanOrEqual(5);
  });

  it("always produces at least one rung", () => {
    mount("<div id='d'>plain</div>");
    expect(computeTargets(document.getElementById("d")!).length).toBeGreaterThanOrEqual(1);
  });
});

describe("password detection", () => {
  it("detects a password field", () => {
    mount("<input id='p' type='password'>");
    expect(isPasswordField(document.getElementById("p")!)).toBe(true);
  });

  it("detects an autocomplete password hint", () => {
    mount("<input id='p' type='text' autocomplete='current-password'>");
    expect(isPasswordField(document.getElementById("p")!)).toBe(true);
  });

  it("does not flag an ordinary field", () => {
    mount("<input id='e' type='email'>");
    expect(isPasswordField(document.getElementById("e")!)).toBe(false);
  });
});
