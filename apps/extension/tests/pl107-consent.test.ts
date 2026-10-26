// PL-107 item 10: a cookie/consent notice in the way is answered, not reported as "something in
// front".
//
// The notice is found by its words and its buttons, never by one site's names or classes. A private
// answer (Reject all / Decline / Necessary only) is clicked and the click is retried; a notice that
// offers only Accept/Allow parks with the sentence that says where to answer it.
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { answerConsentNoticeInPage } from "../src/executor/consent";
import { resetInputClock } from "../src/executor/dispatch";
import { resolveTargetInPage } from "../src/executor/ladder";
import { performStep } from "../src/executor/step-runner";
import type { ExecutorStep } from "../src/executor/types";
import { fakeCdp } from "./helpers/fake-cdp";
import { installLayout, type LayoutOptions } from "./helpers/layout";

let restoreLayout: (() => void) | null = null;

function stamp(html: string, token = "c1"): void {
  document.body.innerHTML = html;
  resolveTargetInPage({ targets: [{ by: "css", selector: "#save" }], within: null, token });
}

function layout(options: LayoutOptions): void {
  restoreLayout?.();
  restoreLayout = installLayout(options);
}

beforeEach(() => {
  document.body.innerHTML = "";
  resetInputClock();
});

afterEach(() => {
  restoreLayout?.();
  restoreLayout = null;
});

describe("the consent notice probe", () => {
  it("answers a notice that offers Reject all", async () => {
    stamp(
      "<button id='save'>Continue</button>" +
        "<div role='dialog' aria-label='cookie consent'>" +
        "<p>We use cookies to improve your experience.</p>" +
        "<button id='accept'>Accept all</button><button id='reject'>Reject all</button></div>",
    );
    let clicked = 0;
    document.getElementById("reject")!.addEventListener("click", () => {
      clicked += 1;
    });
    layout({ hitAt: () => document.getElementById("reject") });
    const { cdp } = fakeCdp();

    const answer = await answerConsentNoticeInPage(cdp, "c1");

    expect(answer).toEqual({ answered: true, parked: false });
    expect(clicked).toBe(1);
  });

  it("parks without clicking when the notice offers only Accept", async () => {
    stamp(
      "<button id='save'>Continue</button>" +
        "<div role='dialog'><p>Your privacy matters. We use cookies.</p>" +
        "<button id='accept'>Accept all</button><button id='customize'>Customize</button></div>",
    );
    let clicked = 0;
    for (const id of ["accept", "customize"]) {
      document.getElementById(id)!.addEventListener("click", () => {
        clicked += 1;
      });
    }
    layout({ hitAt: () => document.getElementById("accept") });
    const { cdp } = fakeCdp();

    expect(await answerConsentNoticeInPage(cdp, "c1")).toEqual({
      answered: false,
      parked: true,
    });
    expect(clicked).toBe(0);
  });

  it("does nothing when the covering element is not a notice", async () => {
    stamp("<button id='save'>Continue</button><div id='plain'>Some other overlay</div>");
    layout({ hitAt: () => document.getElementById("plain") });
    const { cdp } = fakeCdp();

    expect(await answerConsentNoticeInPage(cdp, "c1")).toEqual({
      answered: false,
      parked: false,
    });
  });
});

describe("a click whose target is under a consent notice", () => {
  const step: ExecutorStep = {
    id: "click-1",
    action: "click",
    targets: [{ by: "css", selector: "#save" }],
    timeout_ms: 800,
  };

  it("answers the notice and retries the click once", async () => {
    stamp(
      "<button id='save'>Continue</button>" +
        "<div role='dialog'><p>We use cookies.</p>" +
        "<button id='accept'>Accept all</button><button id='reject'>Reject all</button></div>",
    );
    let noticeGone = false;
    document.getElementById("reject")!.addEventListener("click", () => {
      noticeGone = true;
    });
    layout({
      hitAt: () =>
        noticeGone ? document.querySelector("[data-pl-target]") : document.getElementById("reject"),
    });
    const { cdp, callsTo } = fakeCdp();

    const report = await performStep(step, { row: {} }, undefined, { cdp });

    expect(report.outcome).toBe("done");
    const pressed = callsTo("Input.dispatchMouseEvent").filter(
      (call) => call.params["type"] === "mousePressed",
    );
    expect(pressed).toHaveLength(1);
  });

  it("parks with the cookie sentence when the notice offers only Accept", async () => {
    stamp(
      "<button id='save'>Continue</button>" +
        "<div role='dialog'><p>We value your privacy. Cookies help.</p>" +
        "<button id='accept'>Accept all</button><button id='customize'>Customize</button></div>",
    );
    layout({ hitAt: () => document.getElementById("accept") });
    const { cdp, callsTo } = fakeCdp();

    await expect(performStep(step, { row: {} }, undefined, { cdp })).rejects.toThrow(
      /cookie notice is covering the page/i,
    );
    expect(
      callsTo("Input.dispatchMouseEvent").filter((call) => call.params["type"] === "mousePressed"),
    ).toHaveLength(0);
  });
});
