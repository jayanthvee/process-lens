// PL-114: the executor sets the fields the person clicked, and leaves them alone when they are
// already right.
//
// The fake CDP evaluates page expressions in jsdom, so the real `fieldActionInPage` runs here and
// the real callbacks of a checkbox, a radio and a slider are what the assertions see.
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { resetTokens, runRecipe } from "../src/executor/executor";
import type { ExecutorRecipe } from "../src/executor/types";
import type { LocatorRung, ValueSource } from "../src/shared/recipe-types";
import { fakeCdp } from "./helpers/fake-cdp";
import { installLayout } from "./helpers/layout";

const ORIGIN = "http://localhost:3000";

let restoreLayout: (() => void) | null = null;

function recipe(steps: ExecutorRecipe["steps"]): ExecutorRecipe {
  return {
    format_version: 1,
    meta: { name: "Set a field", allowed_origins: [ORIGIN] },
    steps,
  };
}

function check(
  value: boolean,
  targets: LocatorRung[] = [{ by: "label", text: "Limit registrations" }],
) {
  return {
    id: "tick",
    action: "check",
    name: "Check Limit registrations",
    targets,
    value: { kind: "constant", value } as ValueSource,
  };
}

beforeEach(() => {
  document.body.innerHTML = "";
  window.history.replaceState({}, "", "/settings");
  resetTokens();
  restoreLayout = installLayout();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

describe("a checkbox (PL-114 item 1)", () => {
  it("ticks a box that is unticked", async () => {
    document.body.innerHTML = `
      <label for="limit">Limit registrations</label>
      <input type="checkbox" id="limit" />`;
    const { cdp } = fakeCdp(undefined, { simulateInput: true });

    const summary = await runRecipe(recipe([check(true)]), { row: {} }, { cdp });

    expect(summary.steps[0]?.outcome ?? summary.error).toBe("done");
    expect((document.getElementById("limit") as HTMLInputElement).checked).toBe(true);
  });

  it("leaves an already ticked box ticked -- the flip this fixes", async () => {
    document.body.innerHTML = `
      <label for="limit">Limit registrations</label>
      <input type="checkbox" id="limit" checked />`;
    const { cdp } = fakeCdp(undefined, { simulateInput: true });

    const summary = await runRecipe(recipe([check(true)]), { row: {} }, { cdp });

    expect(summary.steps[0]?.outcome ?? summary.error).toBe("done");
    expect((document.getElementById("limit") as HTMLInputElement).checked).toBe(true);
  });

  it("unticks a box when the row says no", async () => {
    document.body.innerHTML = `
      <label for="limit">Limit registrations</label>
      <input type="checkbox" id="limit" checked />`;
    const { cdp } = fakeCdp(undefined, { simulateInput: true });

    const summary = await runRecipe(recipe([check(false)]), { row: {} }, { cdp });

    expect(summary.steps[0]?.outcome ?? summary.error).toBe("done");
    expect((document.getElementById("limit") as HTMLInputElement).checked).toBe(false);
  });

  it("sets a switch built from a button and an ARIA role", async () => {
    document.body.innerHTML = `
      <button type="button" role="switch" aria-checked="false" aria-label="Weekly digest"
        id="digest">Weekly digest</button>
      <script></script>`;
    const control = document.getElementById("digest") as HTMLElement;
    control.addEventListener("click", () => {
      control.setAttribute("aria-checked", control.getAttribute("aria-checked") === "true" ? "false" : "true");
    });
    const { cdp } = fakeCdp(undefined, { simulateInput: true });

    const summary = await runRecipe(
      recipe([check(true, [{ by: "role", role: "switch", name: "Weekly digest" }])]),
      { row: {} },
      { cdp },
    );

    expect(summary.steps[0]?.outcome ?? summary.error).toBe("done");
    expect(control.getAttribute("aria-checked")).toBe("true");
  });
});

describe("a radio group (PL-114 item 2)", () => {
  function shippingMarkup(): void {
    document.body.innerHTML = `
      <fieldset aria-label="Shipping">
        <legend>Shipping</legend>
        <label><input type="radio" name="ship" value="standard" /> Standard</label>
        <label><input type="radio" name="ship" value="express" /> Express</label>
        <label><input type="radio" name="ship" value="overnight" /> Overnight</label>
      </fieldset>`;
  }

  function choose(value: string) {
    return {
      id: "ship",
      action: "select",
      name: "Select Shipping",
      targets: [{ by: "role", role: "group", name: "Shipping" }] as LocatorRung[],
      value: { kind: "constant", value } as ValueSource,
    };
  }

  it("chooses the option whose label is the row's value", async () => {
    shippingMarkup();
    const { cdp } = fakeCdp(undefined, { simulateInput: true });

    const summary = await runRecipe(recipe([choose("Express")]), { row: {} }, { cdp });

    expect(summary.steps[0]?.outcome ?? summary.error).toBe("done");
    const radios = document.querySelectorAll("input[type='radio']");
    expect((radios[1] as HTMLInputElement).checked).toBe(true);
    expect((radios[0] as HTMLInputElement).checked).toBe(false);
  });

  it("matches a label regardless of case and spacing", async () => {
    shippingMarkup();
    const { cdp } = fakeCdp(undefined, { simulateInput: true });

    await runRecipe(recipe([choose("  overnight ")]), { row: {} }, { cdp });

    expect((document.querySelectorAll("input[type='radio']")[2] as HTMLInputElement).checked).toBe(
      true,
    );
  });

  it("does nothing when the group already shows that value", async () => {
    shippingMarkup();
    (document.querySelectorAll("input[type='radio']")[1] as HTMLInputElement).checked = true;
    const { cdp } = fakeCdp(undefined, { simulateInput: true });

    const summary = await runRecipe(recipe([choose("Express")]), { row: {} }, { cdp });

    expect(summary.steps[0]?.outcome ?? summary.error).toBe("done");
    expect((document.querySelectorAll("input[type='radio']")[1] as HTMLInputElement).checked).toBe(
      true,
    );
  });

  it("parks with every option when the group has no such value", async () => {
    shippingMarkup();
    const { cdp } = fakeCdp(undefined, { simulateInput: true });

    const summary = await runRecipe(recipe([choose("Drone")]), { row: {} }, { cdp });

    expect(summary.state).toBe("failed");
    expect(String(summary.error ?? "")).toContain("Standard");
    expect(String(summary.error ?? "")).toContain("Overnight");
  });

  it("parks a bare number instead of guessing an option by its place (T1 review B2)", async () => {
    // A radio group's options are named; "2" is not one of them, so it is not the second one.
    // Positional matching belongs to a rating row, whose options carry no name of their own. In an
    // ordinary group it would silently pick Goods/Service/Combo's second option for a row that
    // said something else entirely.
    document.body.innerHTML = `
      <fieldset aria-label="Product type">
        <legend>Product type</legend>
        <label><input type="radio" name="kind" value="goods" /> Goods</label>
        <label><input type="radio" name="kind" value="service" /> Service</label>
        <label><input type="radio" name="kind" value="combo" /> Combo</label>
      </fieldset>`;
    const { cdp } = fakeCdp(undefined, { simulateInput: true });

    const summary = await runRecipe(
      recipe([
        {
          id: "kind",
          action: "select",
          targets: [{ by: "role", role: "group", name: "Product type" }] as LocatorRung[],
          value: { kind: "constant", value: "2" } as ValueSource,
        },
      ]),
      { row: {} },
      { cdp },
    );

    expect(summary.state).toBe("failed");
    expect(String(summary.error ?? "")).toContain("Goods");
    expect(String(summary.error ?? "")).toContain("Combo");
    const radios = document.querySelectorAll("input[type='radio']");
    expect(Array.from(radios).some((radio) => (radio as HTMLInputElement).checked)).toBe(false);
  });

  it("chooses in a status bar of aria-checked buttons", async () => {    document.body.innerHTML = `
      <div role="radiogroup" aria-label="Stage">
        <button type="button" role="radio" aria-checked="true">New</button>
        <button type="button" role="radio" aria-checked="false">Booked</button>
      </div>`;
    const buttons = document.querySelectorAll("[role='radio']");
    buttons.forEach((button) =>
      button.addEventListener("click", () => {
        buttons.forEach((other) => other.setAttribute("aria-checked", "false"));
        button.setAttribute("aria-checked", "true");
      }),
    );
    const { cdp } = fakeCdp(undefined, { simulateInput: true });

    const summary = await runRecipe(
      recipe([
        {
          id: "stage",
          action: "select",
          targets: [{ by: "role", role: "radiogroup", name: "Stage" }] as LocatorRung[],
          value: { kind: "constant", value: "Booked" },
        },
      ]),
      { row: {} },
      { cdp },
    );

    expect(summary.steps[0]?.outcome ?? summary.error).toBe("done");
    expect(buttons[1]?.getAttribute("aria-checked")).toBe("true");
  });
});

describe("a row of stars (PL-114 item 5)", () => {
  it("clicks the third star when the row says 3", async () => {
    document.body.innerHTML = `
      <div role="radiogroup" aria-label="Rating">
        ${[1, 2, 3, 4, 5]
          .map(
            (n) =>
              `<button type="button" role="radio" aria-checked="false" aria-label="${n} of 5">&#9733;</button>`,
          )
          .join("")}
      </div>`;
    const stars = document.querySelectorAll("[role='radio']");
    stars.forEach((star) =>
      star.addEventListener("click", () => {
        stars.forEach((other) => other.setAttribute("aria-checked", "false"));
        star.setAttribute("aria-checked", "true");
      }),
    );
    const { cdp } = fakeCdp(undefined, { simulateInput: true });

    const summary = await runRecipe(
      recipe([
        {
          id: "stars",
          action: "select",
          targets: [{ by: "role", role: "radiogroup", name: "Rating" }] as LocatorRung[],
          value: { kind: "constant", value: "3" } as ValueSource,
        },
      ]),
      { row: {} },
      { cdp },
    );

    expect(summary.steps[0]?.outcome ?? summary.error).toBe("done");
    expect(stars[2]?.getAttribute("aria-checked")).toBe("true");
    expect(stars[0]?.getAttribute("aria-checked")).toBe("false");
  });
});

describe("a number the person clicked up or picked (PL-114 item 5)", () => {
  it("sets a range input to the row's number", async () => {
    document.body.innerHTML = `
      <label for="rating">Rating</label>
      <input type="range" id="rating" min="1" max="5" value="1" />`;
    const { cdp } = fakeCdp(undefined, { simulateInput: true });

    const summary = await runRecipe(
      recipe([
        {
          id: "rating",
          action: "fill",
          targets: [{ by: "label", text: "Rating" }] as LocatorRung[],
          value: { kind: "constant", value: 4 },
        },
      ]),
      { row: {} },
      { cdp },
    );

    expect(summary.steps[0]?.outcome ?? summary.error).toBe("done");
    expect((document.getElementById("rating") as HTMLInputElement).value).toBe("4");
  });

  it("presses a stepper up to the row's number", async () => {
    document.body.innerHTML = `
      <div role="group" aria-label="Guests">
        <button type="button" aria-label="minus">-</button>
        <input id="count" aria-label="Guests" value="2" readonly />
        <button type="button" aria-label="plus">+</button>
      </div>`;
    const plus = document.querySelector("button[aria-label='plus']") as HTMLButtonElement;
    const count = document.getElementById("count") as HTMLInputElement;
    plus.addEventListener("click", () => {
      count.value = String(Number(count.value) + 1);
    });
    const { cdp } = fakeCdp(undefined, { simulateInput: true });

    const summary = await runRecipe(
      recipe([
        {
          id: "guests",
          action: "fill",
          targets: [{ by: "role", role: "textbox", name: "Guests" }] as LocatorRung[],
          value: { kind: "constant", value: 5 },
        },
      ]),
      { row: {} },
      { cdp },
    );

    expect(summary.steps[0]?.outcome ?? summary.error).toBe("done");
    expect(count.value).toBe("5");
  });
});
