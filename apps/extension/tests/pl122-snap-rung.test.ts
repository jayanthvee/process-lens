// PL-122: the `snap` rung resolves the element the snapshot tagged, and nothing else.
//
// A token is one element on one page. If the resolver could fall back to a role or a name, the
// model's answer would stop being a pointer and become a guess.
import { beforeEach, describe, expect, it } from "vitest";
import { TARGET_MARKER, resolveTargetInPage } from "../src/executor/ladder";
import { pageSnapshotInPage } from "../src/executor/page-snapshot";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("the snap rung", () => {
  it("resolves only the element that carries the token", () => {
    document.body.innerHTML = `
      <div id="first" role="button" data-pl-snap="e1">Oak Standing Desk</div>
      <div id="second" role="button" data-pl-snap="e2">Oak Standing Desk</div>
      <div id="third" role="button">Oak Standing Desk</div>
    `;

    const result = resolveTargetInPage({
      targets: [{ by: "snap", value: "e1" }],
      token: "t1",
    });

    expect(result.ok).toBe(true);
    expect(result.by).toBe("snap");
    expect(document.querySelector(`[${TARGET_MARKER}="t1"]`)?.id).toBe("first");
    expect(document.querySelectorAll(`[${TARGET_MARKER}="t1"]`).length).toBe(1);
    expect(document.getElementById("second")?.hasAttribute(TARGET_MARKER)).toBe(false);
    expect(document.getElementById("third")?.hasAttribute(TARGET_MARKER)).toBe(false);
  });

  it("resolves a token the page snapshot just wrote", () => {
    document.body.innerHTML = `
      <h2>Products</h2>
      <div role="button" tabindex="0">Oak Standing Desk</div>
      <div role="button" tabindex="0">Mesh Task Chair</div>
    `;
    const snapshot = pageSnapshotInPage({ allowedOrigins: [location.origin], limit: 400 });
    const card = snapshot.elements.find((element) => element.name === "Oak Standing Desk");
    expect(card).toBeDefined();

    const result = resolveTargetInPage({
      targets: [{ by: "snap", value: card!.token }],
      token: "t2",
    });

    expect(result.ok).toBe(true);
    expect(document.querySelector(`[${TARGET_MARKER}="t2"]`)?.textContent).toContain(
      "Oak Standing Desk",
    );
  });

  it("fails when the token is not on the page", () => {
    document.body.innerHTML = `<div role="button" data-pl-snap="e1">Oak Standing Desk</div>`;

    const result = resolveTargetInPage({
      targets: [{ by: "snap", value: "e9" }],
      token: "t3",
    });

    expect(result.ok).toBe(false);
    expect(document.querySelector(`[${TARGET_MARKER}="t3"]`)).toBeNull();
  });
});
