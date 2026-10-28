/**
 * PL-123, T1's price-walk item 8: "Sales Price" also appears in the chatter's change history.
 * The ladder climbs in rung rank, so the field's role and label rungs come before a text-only rung
 * even when the recipe lists the text rung first.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { TARGET_MARKER, resolveTargetInPage } from "../src/executor/ladder";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("a field whose label also appears as page text", () => {
  it("picks the field by its role, not the chatter text", () => {
    document.body.innerHTML = `
      <div class="o-mail-Message">(Sales Price) changed from 910 to 400</div>
      <label for="list_price_0">Sales Price</label>
      <input id="list_price_0" value="400" />
    `;

    const result = resolveTargetInPage({
      targets: [
        { by: "text_fuzzy", value: "Sales Price" },
        { by: "role", role: "textbox", name: "Sales Price" },
        { by: "label", text: "Sales Price" },
      ],
      within: null,
      token: "price",
    });

    expect(result.ok).toBe(true);
    expect(result.by).toBe("role");
    const marked = document.querySelector(`[${TARGET_MARKER}="price"]`);
    expect(marked?.tagName.toLowerCase()).toBe("input");
    expect(marked?.id).toBe("list_price_0");
  });
});
