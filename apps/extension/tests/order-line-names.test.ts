// PL-048: unlabeled fields in an editable table row are named by their column header.
//
// The fixture copies the real Odoo 18 quotation order-line markup (see the fixture header).
// The recorder side is `accessibleName`/`columnHeaderName` in shared/fingerprint.ts; the
// resolver side is `accessibleNameOf`/`roleOf` in executor/ladder.ts. The two sides compute
// the header and the role independently (the resolver is serialized into the page and cannot
// import the recorder's code), so every rule here is pinned from both ends: the same element
// goes through `semanticRole` and through the resolver's role rung, and the same header goes
// through `columnHeaderName` and through a rung named by it.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import {
  accessibleName,
  columnHeaderName,
  computeTargets,
  semanticRole,
} from "../src/shared/fingerprint";
import { TARGET_MARKER, resolveTargetInPage, type ResolveRung } from "../src/executor/ladder";

const FIXTURE_PATH = resolve(__dirname, "fixtures", "odoo-order-lines.html");
const FIXTURE = new DOMParser().parseFromString(readFileSync(FIXTURE_PATH, "utf-8"), "text/html");

/** Mount the fixture's named region, or the whole page. */
function load(scope?: string): void {
  document.body.innerHTML = scope
    ? (FIXTURE.querySelector(scope)?.outerHTML ?? "")
    : FIXTURE.body.innerHTML;
}

function query(targets: ResolveRung[]): Parameters<typeof resolveTargetInPage>[0] {
  return { targets, within: null, token: "pl048" };
}

function marked(): Element | null {
  return document.querySelector(`[${TARGET_MARKER}="pl048"]`);
}

const productInput = (): HTMLInputElement =>
  document.querySelector("td[name='product_template_id'] input") as HTMLInputElement;
const quantityInput = (): HTMLInputElement =>
  document.querySelector("td[name='product_uom_qty'] input") as HTMLInputElement;
const customerInput = (): HTMLInputElement =>
  document.querySelector("#partner_id_0") as HTMLInputElement;

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("part 1: the recorded role and the resolver's role agree", () => {
  it("matches PL-046's recorded rung on the exact recorded element", () => {
    // The rung PL-046 recorded for the product cell's click, run against the element it was
    // recorded from: an input carrying Odoo's own role="combobox" (web AutoComplete template)
    // and the placeholder the account module sets ("Search a product").
    document.body.innerHTML =
      "<div name='product_template_id' class='o_field_widget'>" +
      "<input type='text' class='o-autocomplete--input o_input pe-3' autocomplete='off' " +
      "role='combobox' aria-autocomplete='list' aria-haspopup='listbox' " +
      "placeholder='Search a product' aria-expanded='false'></div>";
    const input = document.querySelector("input")!;
    expect(semanticRole(input)).toBe("combobox");
    const result = resolveTargetInPage(
      query([{ by: "role", role: "combobox", name: "Search a product" }]),
    );
    expect(result.ok).toBe(true);
    expect(marked()).toBe(input);
  });

  it("treats role='label' and malformed roles the same way both sides do", () => {
    // The recorder skips a role="label" and any role that cannot be a recipe rung (the schema's
    // role pattern) and falls back to the tag's implicit role. The resolver now does too: a
    // role rung spelled by the recorder must be the role the resolver reads. The malformed role
    // sits on a button so the rung's implicit role (button) is distinctive; html and body are
    // `generic` with the page's text and would otherwise match a generic rung too.
    document.body.innerHTML =
      "<input id='a' role='label' type='text' aria-label='Phone'>" +
      "<button id='b' role='123 bad!'>Widget</button>";
    expect(semanticRole(document.getElementById("a")!)).toBe("textbox");
    expect(semanticRole(document.getElementById("b")!)).toBe("button");
    expect(
      resolveTargetInPage(query([{ by: "role", role: "textbox", name: "Phone" }])).ok,
    ).toBe(true);
    expect(
      resolveTargetInPage(query([{ by: "role", role: "button", name: "Widget" }])).ok,
    ).toBe(true);
  });

  it("reads a bare <label> as generic, the role the recorder gives it", () => {
    // The recorder never emits role "label" (a click on a label is recorded against the control
    // it labels), so its tag map has no LABEL entry and a label is `generic`. The resolver used
    // to call it "label", so a recorded generic rung could never match it. The form scopes the
    // rung away from body/html, which are generic with the page's text.
    document.body.innerHTML =
      "<form aria-label='Contact'><label id='phone'>Phone <input id='phone-input'></label></form>";
    const label = document.getElementById("phone")!;
    expect(semanticRole(label)).toBe("generic");
    const result = resolveTargetInPage(
      query([{ by: "role", role: "generic", name: "Phone", within: "form:Contact" }]),
    );
    expect(result.ok).toBe(true);
    expect(marked()).toBe(label);
  });
});

describe("part 2: the recorder names a row field by its column header", () => {
  it("records the product field as combobox named Product", () => {
    load("#quote");
    const input = productInput();
    expect(semanticRole(input)).toBe("combobox");
    expect(accessibleName(input)).toBe("Product");
    expect(computeTargets(input)[0]).toEqual({ by: "role", role: "combobox", name: "Product" });
  });

  it("records the quantity field as textbox named Quantity", () => {
    load("#quote");
    const input = quantityInput();
    expect(semanticRole(input)).toBe("textbox");
    expect(accessibleName(input)).toBe("Quantity");
  });

  it("leaves the customer field's rungs unchanged", () => {
    // The customer many2one sits above the table, so no header applies; its name is still the
    // label Odoo renders for it.
    load("#quote");
    expect(accessibleName(customerInput())).toBe("Customer");
    expect(computeTargets(customerInput())[0]).toEqual({
      by: "role",
      role: "combobox",
      name: "Customer",
    });
  });

  it("leaves a read-only row cell's text name alone", () => {
    // The header rule is for field controls; a cell's span is still named by its own text.
    load("#quote");
    const span = document.querySelector(
      "tr[data-id='datapoint_11'] td[name='product_template_id'] span",
    )!;
    expect(accessibleName(span)).toBe("Mesh Task Chair");
  });

  it("leaves sort arrows and icon glyphs out of the header name", () => {
    load("#quote");
    const th = document.querySelector("th[data-name='product_template_id']")!;
    expect(th.querySelector(".o_list_sortable_caret")!.textContent).toContain("\u25BE");
    expect(columnHeaderName(productInput())).toBe("Product");
    expect(columnHeaderName(quantityInput())).toBe("Quantity");
    const price = document.querySelector(
      "tr[data-id='datapoint_12'] td[name='price_unit'] span",
    )!;
    expect(columnHeaderName(price)).toBe("Unit Price");
  });

  it("maps a colspan header to both columns it spans", () => {
    load("#colspan-variant");
    const description = document.querySelector("td[name='name'] input") as HTMLInputElement;
    expect(columnHeaderName(description)).toBe("Product");
  });

  it("uses aria-colindex when the cells carry it", () => {
    load("#colindex-variant");
    const inputs = Array.from(document.querySelectorAll<HTMLInputElement>("#colindex-variant input"));
    expect(columnHeaderName(inputs[0]!)).toBe("SKU");
    expect(columnHeaderName(inputs[1]!)).toBe("Quantity");
  });

  it("returns nothing outside a table cell", () => {
    load("#quote");
    expect(columnHeaderName(customerInput())).toBe("");
  });
});

describe("part 2: the resolver accepts the column-header name on a role rung", () => {
  it("resolves the recorded Product rung to exactly the editing row's input", () => {
    load("#quote");
    const result = resolveTargetInPage(query([{ by: "role", role: "combobox", name: "Product" }]));
    expect(result.ok).toBe(true);
    expect(marked()).toBe(productInput());
  });

  it("resolves the recorded Quantity rung to exactly the row's quantity input", () => {
    load("#quote");
    const result = resolveTargetInPage(query([{ by: "role", role: "textbox", name: "Quantity" }]));
    expect(result.ok).toBe(true);
    expect(marked()).toBe(quantityInput());
  });

  it("keeps placeholder-named rungs resolving", () => {
    // Recipes pinned before PL-048 name the product field by its placeholder; the placeholder
    // is still the element's accessible name, so the old rung must keep working.
    load("#quote");
    const result = resolveTargetInPage(
      query([
        { by: "role", role: "combobox", name: "Search a product" },
        { by: "css", selector: "input.o-autocomplete--input.o_input" },
      ]),
    );
    expect(result.ok).toBe(true);
    expect(marked()).toBe(productInput());
  });

  it("still resolves the customer field by its label name", () => {
    load("#quote");
    const result = resolveTargetInPage(query([{ by: "role", role: "combobox", name: "Customer" }]));
    expect(result.ok).toBe(true);
    expect(marked()).toBe(customerInput());
  });

  it("agrees with the recorder's header name on every field it names", () => {
    // The two sides compute the header independently (the resolver is serialized into the
    // page), so the names are compared on the same elements: a rung named by the recorder's
    // own `columnHeaderName` must resolve to exactly that element.
    load("#colspan-variant");
    const description = document.querySelector("td[name='name'] input") as HTMLInputElement;
    expect(
      resolveTargetInPage(
        query([{ by: "role", role: "textbox", name: columnHeaderName(description) }]),
      ).ok,
    ).toBe(true);
    expect(marked()).toBe(description);
  });
});

describe("part 3: two editable Product fields are still a refusal", () => {
  it("fails with more than one match instead of picking the first", () => {
    load("#quote");
    const editingRow = document.querySelector("tr[data-id='datapoint_12']")!;
    document.querySelector("tbody")!.appendChild(editingRow.cloneNode(true));
    const result = resolveTargetInPage(query([{ by: "role", role: "combobox", name: "Product" }]));
    expect(result.ok).toBe(false);
    expect(result.marker).toBeNull();
    expect(result.attempts[0]).toMatchObject({ matches: 2, reason: "more than one match" });
  });
});
