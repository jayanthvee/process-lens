// PL-107 item 10: a cookie/consent notice in the way is answered, not reported as "something in
// front".
//
// When a click's hit test finds the target covered, this asks the page whether the covering element
// belongs to a consent notice. If the notice offers a private answer -- Reject all, Decline,
// Necessary only, Only essential -- it clicks that and the caller retries the click once. If it only
// offers Accept/Allow/Customize, nothing is clicked for the person: the caller parks with the
// sentence below, so the person answers it in the work window.
//
// The rule is general: the notice is found by its words and its buttons, never by one site's names,
// classes, or URLs.
import type { CdpTransport } from "./cdp";
import { evaluate } from "./page";

/** The sentence a person reads when the notice offers only Accept/Allow. */
export const COOKIE_NOTICE_PARKED =
  "A cookie notice is covering the page. Answer it in the work window, then press Try again.";

export interface ConsentAnswer {
  /** A private answer was clicked; the caller retries the click once. */
  answered: boolean;
  /** The notice offers only Accept/Allow/Customize; the caller parks with the sentence. */
  parked: boolean;
}

/**
 * The page-side probe. Runs in the page: find the marked element, hit-test its box center, and if
 * the hit is outside the target, walk up to a consent notice and answer it the private way.
 *
 * A notice is a dialog/region/banner/aside (or a container whose own class/id/aria-label says
 * cookie/consent/privacy) that mentions cookies/consent/privacy and holds buttons. Only a
 * reject-style button is ever clicked.
 */
function answerConsentNoticeExpression(token: string): string {
  const escaped = String(token).replace(/["\\]/g, "\\$&");
  return `(() => {
    const el = document.querySelector('[data-pl-target="${escaped}"]');
    if (!el || typeof document.elementFromPoint !== "function") return { answered: false, parked: false };
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const hit = document.elementFromPoint(x, y);
    if (!hit || hit === el || el.contains(hit)) return { answered: false, parked: false };

    const noticeWord = /cookie|consent|privacy/i;
    const rejectWord = /^(reject all|reject|decline|refuse|necessary only|only essential|essential only)\\b/i;
    const acceptWord = /^(accept|allow|agree|ok|got it|i agree|allow all|accept all)\\b/i;
    const containerRole = (node) => (node.getAttribute && (node.getAttribute("role") || "")) || "";
    const ownHint = (node) =>
      node.getAttribute && noticeWord.test(
        ((node.getAttribute("aria-label") || "") + " " + (node.getAttribute("id") || "") + " " + (node.className || "")),
      );
    const isNotice = (node) => {
      const tag = (node.tagName || "").toLowerCase();
      const role = containerRole(node).toLowerCase();
      const shaped =
        tag === "dialog" || tag === "aside" || role === "dialog" || role === "region" || role === "banner" || ownHint(node);
      if (!shaped) return false;
      if (!noticeWord.test(node.textContent || "")) return false;
      return node.querySelectorAll("button, [role='button'], a[href], input[type='button'], input[type='submit']").length > 0;
    };
    let notice = null;
    for (let node = hit; node && node !== document.body; node = node.parentElement) {
      if (isNotice(node)) { notice = node; break; }
    }
    if (!notice) return { answered: false, parked: false };

    const buttons = Array.from(
      notice.querySelectorAll("button, [role='button'], a[href], input[type='button'], input[type='submit']"),
    );
    const label = (b) => ((b.textContent || b.value || "").replace(/\\s+/g, " ").trim());
    const reject = buttons.find((b) => rejectWord.test(label(b)) && !acceptWord.test(label(b)));
    if (reject) {
      reject.click();
      return { answered: true, parked: false };
    }
    // Only Accept/Allow/Customize: never answer for the person.
    return { answered: false, parked: true };
  })()`;
}

/** Ask the page whether the covering element is a consent notice, and answer it if private. */
export async function answerConsentNoticeInPage(
  cdp: CdpTransport,
  token: string,
  contextId?: number,
): Promise<ConsentAnswer> {
  const result = (await evaluate(
    cdp,
    answerConsentNoticeExpression(token),
    contextId !== undefined ? { contextId } : {},
  )) as { answered?: unknown; parked?: unknown } | undefined;
  return { answered: result?.answered === true, parked: result?.parked === true };
}
