/**
 * PL-087: the dashboard learns which Chrome it is in, and the runs it starts carry it.
 */

import { afterEach, describe, expect, it } from "vitest";

import { resetBrowserForTests, sessionPath } from "../src/lib/browser";

afterEach(() => resetBrowserForTests(null));

describe("the browser this dashboard is in", () => {
  it("asks about its own Chrome once the extension has answered, and any Chrome before", () => {
    expect(sessionPath(null)).toBe("/api/v1/executor/session");
    expect(sessionPath("browser-anna-0001")).toBe(
      "/api/v1/executor/session?browser=browser-anna-0001",
    );
  });
});
