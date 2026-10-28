/**
 * PL-123 B1: the searched text shows twice -- once as a chip inside the search box, once on the
 * result. A step or condition that means a result never counts the box or its chip.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { conditionExpression } from "../src/executor/assertions";
import {
  TARGET_MARKER,
  resolveTargetInPage,
  resolverExpression,
  type ResolveRung,
} from "../src/executor/ladder";

const ctx = { resolverSource: resolverExpression() };

/** A search box that shows the query as a facet chip, and a result card carrying the same text. */
function searchAndCard(name: string, options: { card?: boolean } = {}): void {
  document.body.innerHTML = `
    <div class="o_searchview">
      <input type="search" role="searchbox" aria-label="Search..." />
      <div class="o_searchview_facet"><span class="o_facet_values">${name}</span></div>
    </div>
    ${options.card === false ? "" : `<div class="o_kanban_record"><span>${name}</span></div>`}
  `;
}

function resolve(targets: ResolveRung[], excludeSearchBox: boolean) {
  return resolveTargetInPage({
    targets,
    within: null,
    token: "probe",
    ...(excludeSearchBox ? { excludeSearchBox: true } : {}),
  });
}

const RESULT: ResolveRung[] = [{ by: "text_fuzzy", value: "Oak Standing Desk" }];

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("a search result is never the search box or its chip", () => {
  it("resolves the card, not the chip, so there is no ambiguity", () => {
    searchAndCard("Oak Standing Desk");

    const result = resolve(RESULT, true);

    expect(result.ok).toBe(true);
    expect(result.attempts[0]).toMatchObject({ matches: 1 });
    const marked = document.querySelector(`[${TARGET_MARKER}="probe"]`);
    expect(marked?.closest(".o_kanban_record")).not.toBeNull();
  });

  it("without the exclusion the page really is ambiguous, so the test proves something", () => {
    searchAndCard("Oak Standing Desk");
    expect(resolve(RESULT, false).ok).toBe(false);
  });

  it("a chip with no result is absent, so the branch parks the row", () => {
    searchAndCard("Oak Standing Desk", { card: false });

    const expression = conditionExpression(
      {
        element_absent: {
          targets: RESULT,
          exclude_search_box: true,
        },
      },
      ctx,
    );

    expect(new Function(`return (${expression})`)()).toBe(true);
  });

  it("the same page without the exclusion would call the chip a result", () => {
    searchAndCard("Oak Standing Desk", { card: false });

    const expression = conditionExpression({ element_absent: { targets: RESULT } }, ctx);

    expect(new Function(`return (${expression})`)()).toBe(false);
  });

  it("a result container that merely holds a search box is still a result", () => {
    document.body.innerHTML = `
      <div class="o_kanban_renderer">
        <div class="o_searchview"><input type="search" role="searchbox" /></div>
        <div class="o_kanban_record"><span>Oak Standing Desk</span></div>
      </div>
    `;

    const result = resolve(RESULT, true);

    expect(result.ok).toBe(true);
    const marked = document.querySelector(`[${TARGET_MARKER}="probe"]`);
    expect(marked?.closest(".o_kanban_record")).not.toBeNull();
  });
});
