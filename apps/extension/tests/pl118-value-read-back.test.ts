// PL-118 items 1 and 3: a read-back compares what a person reads, not the exact string.
//
// The app formats numbers ("$ 799.00", "1,299.00"), so 799 and "$ 799.00" are the same value. Text
// is compared trimmed and, when the recipe says so, without case. The actual value the page shows
// comes back with the check, so a try can report "still 910, not saved".
import { beforeEach, describe, expect, it } from "vitest";

import { conditionExpression, readValueEquals } from "../src/executor/assertions";
import { resolverExpression } from "../src/executor/ladder";
import type { Condition } from "../src/shared/recipe-types";
import { fakeCdp } from "./helpers/fake-cdp";

const ctx = { resolverSource: resolverExpression() };

function run(expression: string): unknown {
  return new Function(`return (${expression})`)();
}

function valueEquals(value: string, options: { caseInsensitive?: boolean } = {}): Condition {
  return {
    value_equals: {
      selector: {
        targets: [{ by: "css", selector: "#price" }],
        description: "Sales price",
      },
      value,
      ...(options.caseInsensitive === true ? { case_insensitive: true } : {}),
    },
  };
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("value_equals reads a person's value", () => {
  it("treats 799 and $ 799.00 as the same number", () => {
    document.body.innerHTML = "<input id='price' value='$ 799.00'>";
    expect(run(conditionExpression(valueEquals("799"), ctx))).toBe(true);
  });

  it("ignores thousands separators and spaces", () => {
    document.body.innerHTML = "<input id='price' value='$ 1,299.00'>";
    expect(run(conditionExpression(valueEquals("1299"), ctx))).toBe(true);
  });

  it("compares text trimmed and without case when the recipe says so", () => {
    document.body.innerHTML = "<div id='price'>  Pending </div>";
    expect(run(conditionExpression(valueEquals("pending", { caseInsensitive: true }), ctx))).toBe(
      true,
    );
    // Without the flag, the old exact comparison stays.
    expect(run(conditionExpression(valueEquals("pending"), ctx))).toBe(false);
  });

  it("does not call two different amounts equal", () => {
    document.body.innerHTML = "<input id='price' value='910'>";
    expect(run(conditionExpression(valueEquals("799"), ctx))).toBe(false);
  });

  it("does not read a decimal comma as a thousands separator (S1)", () => {
    // "799,50" is 799.5 in half of Europe, not 79950. A comma is a thousands separator only
    // before exactly three digits.
    document.body.innerHTML = "<input id='price' value='799,50'>";
    expect(run(conditionExpression(valueEquals("79950"), ctx))).toBe(false);
  });

  it("does not match a row's 15 against a page showing 1,5 (S1)", () => {
    document.body.innerHTML = "<input id='price' value='1,5'>";
    expect(run(conditionExpression(valueEquals("15"), ctx))).toBe(false);
  });
});

describe("readValueEquals", () => {
  it("returns the page's value and its field's name", async () => {
    document.body.innerHTML = "<input id='price' value='$ 799.00'>";
    const { cdp } = fakeCdp();

    const read = await readValueEquals(cdp, valueEquals("799"), ctx);

    expect(read).toEqual({ label: "Sales price", value: "$ 799.00" });
  });

  it("returns null when the field is not on this page", async () => {
    document.body.innerHTML = "<p>Somewhere else</p>";
    const { cdp } = fakeCdp();

    expect(await readValueEquals(cdp, valueEquals("799"), ctx)).toBeNull();
  });
});
