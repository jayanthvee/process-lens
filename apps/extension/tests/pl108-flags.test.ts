// PL-108: the experiment's switch is off unless it is explicitly on.
import { describe, expect, it } from "vitest";

import {
  DEFAULT_DISPATCH_PATH,
  dispatchPathFrom,
  inPageDispatchEnabled,
  IN_PAGE_DISPATCH_KEY,
} from "../src/executor/flags";

describe("the in-page dispatch flag", () => {
  it("is off by default and for anything that is not an explicit yes", () => {
    expect(DEFAULT_DISPATCH_PATH).toBe("debugger");
    for (const value of [undefined, null, false, "off", "0", "", "debugger", 1, {}]) {
      expect(dispatchPathFrom(value)).toBe("debugger");
      expect(inPageDispatchEnabled(value)).toBe(false);
    }
  });

  it("turns on only for true or an explicit yes word", () => {
    for (const value of [true, "in-page", "inpage", "on", "true", "1", " IN-PAGE "]) {
      expect(dispatchPathFrom(value)).toBe("in-page");
      expect(inPageDispatchEnabled(value)).toBe(true);
    }
  });

  it("names its storage key", () => {
    expect(IN_PAGE_DISPATCH_KEY).toBe("processlens.inPageDispatch");
  });
});
