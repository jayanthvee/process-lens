// PL-113 S3: the two causes a hit-test miss can have, on a real DOM.
//
// 1. A button that goes disabled / aria-busy / pointer-events: none as it is clicked. A common
//    button style does exactly that, and `elementFromPoint` then passes through the button and
//    returns the card behind it, which read as "covered".
// 2. A full-height layout (height: 100vh; overflow: hidden) with its own scrolling panel. The
//    button sits below the window, `scrollIntoView` on the document does nothing, and the hit test
//    finds nothing -- reported as "something was in front of it" with nothing there.
//
// The markup is ordinary application markup, not one site's.
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { measureTargetInPage } from "../src/executor/ladder";
import { installLayout } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

describe("a click that did not land (PL-113 S3)", () => {
  it("calls a button that went busy busy, not covered by the card behind it", () => {
    document.body.innerHTML = `
      <div id="card">
        <button data-pl-target="save" id="save" disabled aria-busy="true">Save</button>
      </div>`;
    const button = document.getElementById("save") as HTMLButtonElement;
    // The style that makes this happen: a button that is visibly busy and takes no clicks.
    button.style.pointerEvents = "none";
    // The point passes through the button and lands on the card behind it.
    restoreLayout = installLayout({
      hitAt: () => document.getElementById("card") as Element,
    });

    const result = measureTargetInPage({ token: "save" });

    expect(result.ok).toBe(false);
    expect(result.reason).toContain("busy");
    expect(result.reason).not.toContain("covered");
  });

  it("scrolls the nearest scrolling panel, then measures again", () => {
    document.body.innerHTML = `
      <div id="shell" style="height: 100vh; overflow: hidden">
        <div id="panel" style="overflow-y: auto">
          <button data-pl-target="save" id="save">Save</button>
        </div>
      </div>`;
    const panel = document.getElementById("panel") as HTMLElement;
    const button = document.getElementById("save") as HTMLElement;
    // jsdom has no layout: the panel is a tall scroll region and the button starts below the
    // window. Scrolling the panel is what brings it into view.
    Object.defineProperty(panel, "scrollHeight", { value: 2400, configurable: true });
    Object.defineProperty(panel, "clientHeight", { value: 600, configurable: true });
    panel.scrollTop = 0;

    restoreLayout = installLayout({
      rectAt: (_call, element) => {
        if (element !== button && element !== panel) return { top: 0, left: 0, width: 0, height: 0 };
        const top = panel.scrollTop > 0 ? 300 : 900;
        return { top, left: 10, width: 120, height: 24 };
      },
      hitAt: (_call, _x, y) => (y < window.innerHeight ? button : null),
    });

    const result = measureTargetInPage({ token: "save" });

    expect(result.ok).toBe(true);
    expect(panel.scrollTop).toBeGreaterThan(0);
  });

  it("says the button is outside the window when there is nothing to scroll", () => {
    document.body.innerHTML = `<button data-pl-target="save" id="save">Save</button>`;
    restoreLayout = installLayout({
      rectAt: () => ({ top: 900, left: 10, width: 120, height: 24 }),
      hitAt: () => null,
    });

    const result = measureTargetInPage({ token: "save" });

    expect(result.ok).toBe(false);
    expect(result.reason).toBe("the target is outside the window and the page didn't scroll to it");
  });
});
