// PL-122: the page snapshot a run sends when the remembered target is gone.
//
// The snapshot is the only page data a model sees. These tests pin the three things it must never
// carry -- a password field, a typed value, a hidden input -- and the origin and size rules.
import { beforeEach, describe, expect, it } from "vitest";
import { pageSnapshotInPage, snapshotExpression } from "../src/executor/page-snapshot";

beforeEach(() => {
  document.body.innerHTML = "";
});

function names(result: ReturnType<typeof pageSnapshotInPage>): string[] {
  return result.elements.map((element) => element.name);
}

describe("the page snapshot", () => {
  it("never carries a password field, an input's value or a hidden input", () => {
    document.body.innerHTML = `
      <h2>Products</h2>
      <input id="search" type="search" aria-label="Search..." value="typed-secret-799">
      <input id="password" type="password" aria-label="Password" value="hunter2">
      <input id="hidden" type="hidden" value="hidden-secret">
      <textarea id="notes" aria-label="Notes">typed note</textarea>
      <div role="button" tabindex="0"><span>Oak Standing Desk</span><span>799</span></div>
      <button id="disabled" disabled>Save</button>
    `;

    const result = pageSnapshotInPage({ allowedOrigins: [location.origin], limit: 400 });
    const serialized = JSON.stringify(result);

    expect(result.ok).toBe(true);
    expect(serialized).not.toContain("hunter2");
    expect(serialized).not.toContain("typed-secret-799");
    expect(serialized).not.toContain("hidden-secret");
    expect(serialized).not.toContain("typed note");
    expect(names(result)).not.toContain("Password");
    expect(result.elements.some((element) => element.tag === "input" && element.name === "Notes")).toBe(
      false,
    );

    const card = result.elements.find((element) => element.name === "Oak Standing Desk");
    expect(card?.visible).toBe(true);
    expect(card?.enabled).toBe(true);
    expect(card?.section).toBe("Products");

    const disabled = result.elements.find((element) => element.name === "Save");
    expect(disabled?.enabled).toBe(false);
  });

  it("writes a short token onto every element it returns", () => {
    document.body.innerHTML = `
      <button>Save</button>
      <a href="/products">Products</a>
    `;

    const result = pageSnapshotInPage({ allowedOrigins: [location.origin], limit: 400 });

    expect(result.elements.length).toBe(2);
    const tokens = new Set(result.elements.map((element) => element.token));
    expect(tokens.size).toBe(2);
    for (const element of result.elements) {
      const tagged = document.querySelector(`[data-pl-snap="${element.token}"]`);
      expect(tagged).not.toBeNull();
    }
  });

  it("returns nothing from a page outside the allowed origins", () => {
    document.body.innerHTML = "<button>Delete everything</button>";

    const result = pageSnapshotInPage({
      allowedOrigins: ["https://allowed.example"],
      limit: 400,
    });

    expect(result.ok).toBe(false);
    expect(result.elements).toEqual([]);
    expect(JSON.stringify(result)).not.toContain("Delete everything");
  });

  it("caps the snapshot and marks it truncated", () => {
    document.body.innerHTML = Array.from(
      { length: 450 },
      (_value, index) => `<button>Item ${index}</button>`,
    ).join("");

    const result = pageSnapshotInPage({ allowedOrigins: [location.origin], limit: 400 });

    expect(result.elements.length).toBe(400);
    expect(result.truncated).toBe(true);
    expect(new Set(result.elements.map((element) => element.token)).size).toBe(400);
  });

  it("serializes to a self-contained page function", () => {
    // The real executor serializes this function with `toString()` and evaluates it in the page.
    // A closure over a module constant would work in this test and fail in Chrome.
    document.body.innerHTML = "<button>Save</button>";
    const pageFunction = new Function(`return ${snapshotExpression()}`)() as typeof pageSnapshotInPage;

    const result = pageFunction({ allowedOrigins: [location.origin], limit: 400 });

    expect(result.elements.some((element) => element.name === "Save")).toBe(true);
  });

  it("does not read into another frame", () => {
    const frame = document.createElement("iframe");
    document.body.appendChild(frame);
    frame.contentDocument!.body.innerHTML = "<button>Another origin</button>";
    document.body.insertAdjacentHTML("beforeend", "<button>Our page</button>");

    const result = pageSnapshotInPage({ allowedOrigins: [location.origin], limit: 400 });

    expect(names(result)).toContain("Our page");
    expect(names(result)).not.toContain("Another origin");
  });
});
