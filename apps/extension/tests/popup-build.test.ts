// PL-100: the popup says which build it is.
//
// A tester's notes have to be able to name the build they came from. A packaged build's manifest
// carries `version_name` (`0.1.1 (20261025 c8abcb3)`), which the popup reads through
// `chrome.runtime.getManifest()`; an unstamped `npm run build` falls back to `version`. This file
// drives the popup's real markup with both shapes, so what is asserted is the sentence a person
// reads at the foot of the popup.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { beforeEach, describe, expect, it } from "vitest";

import { buildLabel, renderBuildLabel } from "../src/popup/build-label";

const POPUP_HTML = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../src/popup/popup.html"),
  "utf8",
);
const POPUP_TS = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../src/popup/popup.ts"),
  "utf8",
);
const POPUP_BODY = /<body[^>]*>([\s\S]*?)<\/body>/.exec(POPUP_HTML)?.[1] ?? POPUP_HTML;

beforeEach(() => {
  document.body.innerHTML = POPUP_BODY;
});

describe("the build at the foot of the popup", () => {
  it("sits at the bottom of the panel", () => {
    const body = new DOMParser().parseFromString(POPUP_HTML, "text/html").body;
    const panel = body.querySelector("main.panel");
    expect(panel?.lastElementChild?.id).toBe("build");
    expect(body.querySelectorAll("#build")).toHaveLength(1);
  });

  it("shows the version and the stamp Chrome shows", () => {
    renderBuildLabel(document, {
      version: "0.1.1",
      version_name: "0.1.1 (20261025 c8abcb3)",
    });

    expect(document.getElementById("build")?.textContent).toBe(
      "ProcessLens 0.1.1 (20261025 c8abcb3)",
    );
  });

  it("reads the build from the manifest Chrome gives the popup", () => {
    // The label is only right if the popup asks Chrome, rather than naming a version in its own
    // source: a packaged build's `version_name` is what makes the popup and chrome://extensions
    // agree (PL-100).
    expect(POPUP_TS).toContain("chrome.runtime.getManifest()");
    expect(POPUP_TS).toContain("renderBuildLabel(document, chrome.runtime.getManifest())");
  });

  it("falls back to the plain version when a build carries no stamp", () => {
    renderBuildLabel(document, { version: "0.1.0" });
    expect(document.getElementById("build")?.textContent).toBe("ProcessLens 0.1.0");

    // Chrome also accepts an empty version_name; it means the same as none.
    expect(buildLabel({ version: "0.1.0", version_name: "   " })).toBe("ProcessLens 0.1.0");
  });
});
