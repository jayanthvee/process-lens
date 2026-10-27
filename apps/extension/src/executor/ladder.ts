// The target resolution engine: the 5-rung ladder, climbing from the most
// semantic way of finding an element to the least.
//
// The resolver runs inside the target page, not in the extension. It is written
// as one self-contained function so it can be serialized with `.toString()` and
// evaluated in a page's context with `Runtime.evaluate`; the executor calls it
// through `resolveTarget`, and the unit tests call it directly against a
// document. It never reads or writes anything outside the page it runs in.
//
// A rung counts as found only when exactly one visible, enabled element matches
// it; an ambiguous rung is recorded and the ladder moves on, so a locator that
// could hit either of two elements can never be clicked.

/** The ladder, in the order the recipe format defines. */
export const LADDER: readonly string[] = ["role", "testid", "label", "css", "text_fuzzy"];

/** The attribute the resolver stamps on the element it found. */
export const TARGET_MARKER = "data-pl-target";

/** One rung of a target ladder, as stored in the recipe. */
export interface ResolveRung {
  by: string;
  role?: string;
  name?: string;
  value?: string;
  text?: string;
  selector?: string;
  threshold?: number;
  within?: string;
}

/** The query handed to the page-side resolver. */
export interface ResolveQuery {
  targets: ResolveRung[];
  /** Container scope for the whole bundle, e.g. "form:New lead". A rung overrides it. */
  within?: string | null;
  /** The token to stamp on the found element. */
  token: string;
}

export interface RungAttempt {
  rung: number;
  by: string;
  matches: number;
  /** How many the rung matched before visibility and enabledness were applied. */
  found?: number;
  reason?: string;
}

export interface ResolveResult {
  ok: boolean;
  rung: number;
  by: string;
  marker: string | null;
  /** The box center in CSS pixels, when the page has layout (0,0 in jsdom). */
  x: number;
  y: number;
  tag: string;
  /**
   * The element's accessible name, when a rung matched. The destructive-control guard is about the
   * control a person sees, not about what the ladder happened to declare, so it needs this.
   */
  name?: string;
  /** The matched element's role, as the ladder computes it (PL-066: an option is never destructive). */
  role?: string;
  attempts: RungAttempt[];
  error?: string;
}

/** The query handed to the page-side measurement: the marked element's token. */
export interface MeasureQuery {
  token: string;
}

/**
 * The result of measuring a marked element just before a mouse click.
 *
 * `hit` is the tag of whatever `document.elementFromPoint` returned at the box
 * center, so a caller can see what a click would actually land on. A click is
 * only safe when the measured box has area and the hit test lands on the target
 * (or inside it).
 */
export interface MeasureResult {
  ok: boolean;
  x: number;
  y: number;
  width: number;
  height: number;
  /** The tag of the element at the box center, or "" when the hit test is unavailable. */
  hit: string;
  /**
   * True when `x`/`y` are top-viewport coordinates: the top document, or a
   * same-origin frame whose iframe offsets were added (PL-018b). A click is
   * dispatched in the top viewport, so a target in a cross-origin frame, whose
   * offset can't be read, is refused by `dispatchClick`.
   */
  topFrame: boolean;
  reason?: string;
}

/**
 * Scroll a marked element into view, re-measure its box, and hit-test the box
 * center. Runs in the page.
 *
 * This is separate from the resolver because a click needs the box *after*
 * scrolling, and needs to know that a click at that point would land on the
 * target rather than on something covering it. A zero-area box or a hit-test
 * miss is a refusal, not a click at (0, 0).
 */
export function measureTargetInPage(query: MeasureQuery): MeasureResult {
  const MARKER = "data-pl-target";
  // PL-018b: a click is dispatched in TOP-viewport coordinates, but this document measures in its
  // own viewport. In a same-origin frame, each enclosing iframe's content-box origin is added on the
  // way up. A cross-origin parent hides `frameElement` (it reads null), so no offset is known and
  // the coordinates are not the top viewport's: `topFrame` is then false and a click is refused.
  const frameOffset = (): { dx: number; dy: number } | null => {
    let dx = 0;
    let dy = 0;
    try {
      let win: Window = window;
      while (win !== win.top) {
        const host = win.frameElement as HTMLElement | null;
        if (!host) return null;
        const box = host.getBoundingClientRect();
        const style = win.parent.getComputedStyle(host);
        dx += box.left + host.clientLeft + (parseFloat(style.paddingLeft) || 0);
        dy += box.top + host.clientTop + (parseFloat(style.paddingTop) || 0);
        win = win.parent;
      }
    } catch {
      return null;
    }
    return { dx, dy };
  };
  const topFrame = frameOffset() !== null;
  const toTop = (x: number, y: number): { x: number; y: number } => {
    const offset = frameOffset() ?? { dx: 0, dy: 0 };
    return { x: x + offset.dx, y: y + offset.dy };
  };
  const reject = (reason: string): MeasureResult => ({
    ok: false,
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    hit: "",
    topFrame,
    reason,
  });

  // The marked element can live inside an OPEN shadow root, which `document.querySelector` cannot
  // see past. The light tree is searched first — the same element a plain document query finds, and
  // an invalid token still throws there — then each host's open shadow root, in document order. A
  // closed root is invisible (`el.shadowRoot` is null), so nothing inside it is found.
  const deepMarker = (selector: string): Element | null => {
    const light = document.querySelector(selector);
    if (light) return light;
    const roots: ShadowRoot[] = [];
    const collect = (root: Document | ShadowRoot): void => {
      for (const el of Array.from(root.querySelectorAll("*"))) {
        const shadow = el.shadowRoot;
        if (shadow) {
          roots.push(shadow);
          collect(shadow);
        }
      }
    };
    collect(document);
    for (const shadow of roots) {
      const found = shadow.querySelector(selector);
      if (found) return found;
    }
    return null;
  };

  let element: Element | null = null;
  try {
    const escaped = String(query.token).replace(/["\\]/g, "\\$&");
    element = deepMarker(`[${MARKER}="${escaped}"]`);
  } catch {
    return reject("the marker token is not a valid selector");
  }
  if (!element) return reject("the marked element is no longer in the document");

  // Scroll first, so the measured box is where a click will land.
  if (typeof element.scrollIntoView === "function") {
    try {
      element.scrollIntoView({ block: "center", inline: "center" });
    } catch {
      // A page can refuse scrolling; the box below is still measured.
    }
  }

  // PL-113 S3: a control that is disabled, busy, or taking no pointer events is not "covered".
  // A very common button style sets `pointer-events: none` and `disabled` at the moment it is
  // clicked, and `elementFromPoint` then passes through it and returns the card behind it. Telling
  // the two apart is the difference between "something is in the way" and "the app is working".
  const busyReason = (el: Element): string | null => {
    const style =
      typeof window.getComputedStyle === "function" ? window.getComputedStyle(el) : null;
    const disabled =
      (el as HTMLButtonElement).disabled === true ||
      el.getAttribute("aria-disabled") === "true" ||
      el.hasAttribute("disabled");
    const busy = el.getAttribute("aria-busy") === "true";
    const inert = style !== null && style.pointerEvents === "none";
    if (!disabled && !busy && !inert) return null;
    const why = disabled ? "disabled" : busy ? "busy" : "not taking clicks";
    return `the target is busy (${why})`;
  };

  // PL-113 S3: the covering element's own name or text, so a sentence can name it; its kind alone
  // when it has none.
  const describeElement = (el: Element): string => {
    const tag = el.tagName.toLowerCase();
    const label = (el.getAttribute("aria-label") ?? "").replace(/\s+/g, " ").trim();
    const text = (el.textContent ?? "").replace(/\s+/g, " ").trim();
    const name = (label || text).slice(0, 60);
    return name ? `<${tag} "${name}">` : `<${tag}>`;
  };

  // PL-113 S3: a full-height layout (100vh with its own scrolling panel) puts the target below the
  // window. `scrollIntoView` on the document does nothing there, because the document is not what
  // scrolls: the nearest scrollable ancestor is. Returns whether it moved anything.
  const scrollAncestor = (el: Element): boolean => {
    const box = el.getBoundingClientRect();
    const viewHeight = window.innerHeight || 0;
    if (box.top >= 0 && box.bottom <= viewHeight) return false;
    let node: Element | null = el.parentElement;
    while (node !== null) {
      const ancestor: Element = node;
      const style =
        typeof window.getComputedStyle === "function"
          ? window.getComputedStyle(ancestor)
          : null;
      if (style !== null && /(auto|scroll)/.test(style.overflowY)) {
        const height = ancestor.clientHeight;
        const total = ancestor.scrollHeight;
        if (total > height) {
          const delta = box.top + box.height / 2 - viewHeight / 2;
          ancestor.scrollTop = Math.max(0, Math.min(total - height, ancestor.scrollTop + delta));
          return true;
        }
      }
      node = ancestor.parentElement;
    }
    return false;
  };

  let rect = element.getBoundingClientRect();
  let width = rect.width;
  let height = rect.height;
  if (!(width > 0) || !(height > 0)) {
    return reject(`the element has no clickable area (${width}x${height})`);
  }

  let x = rect.left + width / 2;
  let y = rect.top + height / 2;

  // A hit test confirms nothing covers the target. jsdom has no layout and no
  // elementFromPoint; in a browser it is present, so the check is real wherever
  // a click can actually be dispatched.
  const fromPoint =
    typeof document.elementFromPoint === "function" ? document.elementFromPoint : null;
  if (fromPoint) {
    let hit = fromPoint.call(document, x, y);
    if (!hit) {
      // Nothing at that point at all: the target is below (or beside) the window and the page did
      // not scroll to it. Scroll the panel that actually scrolls, then measure once more.
      if (!scrollAncestor(element)) {
        return reject("the target is outside the window and the page didn't scroll to it");
      }
      rect = element.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      x = rect.left + width / 2;
      y = rect.top + height / 2;
      hit = fromPoint.call(document, x, y);
      if (!hit) {
        return reject("the target is outside the window and the page didn't scroll to it");
      }
    }
    // A busy control is the app still working, not something in the way. Checked before the
    // coverage sentence, because the point passing through such a control makes it look covered.
    const busy = busyReason(element);
    if (busy !== null) return reject(busy);
    // Only the target itself, a descendant of it, or the host of the shadow tree it lives in is
    // a hit. An ANCESTOR is not: a click at the box center would land on the wrapper that
    // contains the target. A shadow target reports its host here, because the point lookup
    // retargets across the shadow boundary — that is the target's own tree, not something
    // covering it. The chain is walked with `parentElement`/`parentNode` (a shadow root needs no
    // `getRootNode`), so the same code runs in a browser and in a jsdom test.
    const hostChain: Element[] = [];
    let node: Element | null = element;
    while (node) {
      const parent: Element | null = node.parentElement;
      if (parent) {
        node = parent;
        continue;
      }
      const rootNode: Node | null = node.parentNode;
      const hostEl: Element | null =
        rootNode && (rootNode as ShadowRoot).host ? (rootNode as ShadowRoot).host : null;
      if (!hostEl) break;
      hostChain.push(hostEl);
      node = hostEl;
    }
    if (hit !== element && !element.contains(hit) && !hostChain.includes(hit)) {
      return reject(`the box center is covered by ${describeElement(hit)}`);
    }
    return { ok: true, ...toTop(x, y), width, height, hit: hit.tagName.toLowerCase(), topFrame };
  }
  return { ok: true, ...toTop(x, y), width, height, hit: "", topFrame };
}

/** The query handed to the page-side settle: the marked element's token and the time it may take. */
export interface SettleQuery {
  token: string;
  /** How long, in milliseconds, the box may take to stop moving and the point to clear. */
  deadline_ms: number;
}

/**
 * Wait for a marked element to stop moving. Runs in the page and returns a Promise, so it is
 * evaluated with `awaitPromise`.
 *
 * The element is scrolled into view once. Its box is then measured on two consecutive animation
 * frames — a `requestAnimationFrame` pair — and the box is STABLE when x, y, width and height each
 * differ by 1 px or less across the pair; the settled box is the second frame's. Nothing else is
 * judged here — the point is hit-tested separately, immediately before the press (see
 * `hitTestTargetInPage`). A box that never settles until `deadline_ms` is "kept moving". Nothing
 * here sends input.
 */
export function settleTargetInPage(query: SettleQuery): Promise<MeasureResult> {
  const MARKER = "data-pl-target";
  // PL-018b: a click is dispatched in TOP-viewport coordinates, but this document measures in its
  // own viewport. In a same-origin frame, each enclosing iframe's content-box origin is added on the
  // way up. A cross-origin parent hides `frameElement` (it reads null), so no offset is known and
  // the coordinates are not the top viewport's: `topFrame` is then false and a click is refused.
  const frameOffset = (): { dx: number; dy: number } | null => {
    let dx = 0;
    let dy = 0;
    try {
      let win: Window = window;
      while (win !== win.top) {
        const host = win.frameElement as HTMLElement | null;
        if (!host) return null;
        const box = host.getBoundingClientRect();
        const style = win.parent.getComputedStyle(host);
        dx += box.left + host.clientLeft + (parseFloat(style.paddingLeft) || 0);
        dy += box.top + host.clientTop + (parseFloat(style.paddingTop) || 0);
        win = win.parent;
      }
    } catch {
      return null;
    }
    return { dx, dy };
  };
  const topFrame = frameOffset() !== null;
  const toTop = (x: number, y: number): { x: number; y: number } => {
    const offset = frameOffset() ?? { dx: 0, dy: 0 };
    return { x: x + offset.dx, y: y + offset.dy };
  };
  const failure = (reason: string): MeasureResult => ({
    ok: false,
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    hit: "",
    topFrame,
    reason,
  });

  // The marked element can live inside an OPEN shadow root, which `document.querySelector` cannot
  // see past. The light tree is searched first — the same element a plain document query finds, and
  // an invalid token still throws there — then each host's open shadow root, in document order. A
  // closed root is invisible (`el.shadowRoot` is null), so nothing inside it is found.
  const deepMarker = (selector: string): Element | null => {
    const light = document.querySelector(selector);
    if (light) return light;
    const roots: ShadowRoot[] = [];
    const collect = (root: Document | ShadowRoot): void => {
      for (const el of Array.from(root.querySelectorAll("*"))) {
        const shadow = el.shadowRoot;
        if (shadow) {
          roots.push(shadow);
          collect(shadow);
        }
      }
    };
    collect(document);
    for (const shadow of roots) {
      const found = shadow.querySelector(selector);
      if (found) return found;
    }
    return null;
  };

  let found: Element | null = null;
  try {
    const escaped = String(query.token).replace(/["\\]/g, "\\$&");
    found = deepMarker(`[${MARKER}="${escaped}"]`);
  } catch {
    return Promise.resolve(failure("the marker token is not a valid selector"));
  }
  if (!found) return Promise.resolve(failure("the marked element is no longer in the document"));
  const target: Element = found;

  if (typeof target.scrollIntoView === "function") {
    try {
      target.scrollIntoView({ block: "center", inline: "center" });
    } catch {
      // A page can refuse scrolling; the box below is still measured.
    }
  }

  const clock = (): number =>
    typeof performance !== "undefined" && typeof performance.now === "function"
      ? performance.now()
      : Date.now();
  const budget = Number(query.deadline_ms);
  const limit = Number.isFinite(budget) && budget > 0 ? budget : 0;
  const started = clock();

  const measure = (): { left: number; top: number; width: number; height: number } => {
    const rect = target.getBoundingClientRect();
    return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
  };
  // One animation frame. `requestAnimationFrame` is the real path; a timer stands in only where the
  // page has none. A frame that never comes (a hidden tab pauses them) resolves `false` when the
  // deadline passes, so the wait cannot hang and a missing frame is never read as a still box.
  const nextFrame = (): Promise<boolean> =>
    new Promise<boolean>((resolve) => {
      if (typeof requestAnimationFrame !== "function") {
        setTimeout(() => resolve(true), 0);
        return;
      }
      const remaining = Math.max(50, limit - (clock() - started));
      const guard = setTimeout(() => resolve(false), remaining);
      requestAnimationFrame(() => {
        clearTimeout(guard);
        resolve(true);
      });
    });

  const first = measure();
  if (!(first.width > 0) || !(first.height > 0)) {
    return Promise.resolve(
      failure(`the element has no clickable area (${first.width}x${first.height})`),
    );
  }

  // One box, measured on the NEXT animation frame. A frame that never comes (a hidden tab pauses
  // them) resolves `null`, so the wait cannot hang and a missing frame is never read as a still box.
  const measureAtFrame = (): Promise<{ left: number; top: number; width: number; height: number } | null> =>
    nextFrame().then((framed) => (framed && target.isConnected ? measure() : null));

  const within = (
    a: { left: number; top: number; width: number; height: number },
    b: { left: number; top: number; width: number; height: number },
  ): boolean =>
    Math.abs(a.left - b.left) <= 1 &&
    Math.abs(a.top - b.top) <= 1 &&
    Math.abs(a.width - b.width) <= 1 &&
    Math.abs(a.height - b.height) <= 1;

  // A PAIR of consecutive animation frames is the unit of comparison: the box on frame A versus the
  // box on frame B. When the pair holds still, the target has settled.
  const attempt = (): Promise<MeasureResult> =>
    measureAtFrame().then((frameA): MeasureResult | Promise<MeasureResult> => {
      if (!frameA) {
        return failure("the target kept moving: no animation frame arrived to measure it");
      }
      if (!(frameA.width > 0) || !(frameA.height > 0)) {
        return failure(`the element has no clickable area (${frameA.width}x${frameA.height})`);
      }
      return measureAtFrame().then((frameB): MeasureResult | Promise<MeasureResult> => {
        if (!frameB) {
          return failure("the target kept moving: no animation frame arrived to measure it");
        }
        if (within(frameA, frameB)) {
          if (!(frameB.width > 0) || !(frameB.height > 0)) {
            return failure(`the element has no clickable area (${frameB.width}x${frameB.height})`);
          }
          return {
            ok: true,
            ...toTop(frameB.left + frameB.width / 2, frameB.top + frameB.height / 2),
            width: frameB.width,
            height: frameB.height,
            hit: "",
            topFrame,
          };
        }
        if (clock() - started >= limit) {
          return failure(
            "the target kept moving: its box did not hold still across two animation frames",
          );
        }
        return attempt();
      });
    });

  return attempt();
}

/**
 * Hit-test the point a click would land on: re-measure the marked element's box and check that
 * `elementFromPoint` at its center returns the element itself or a descendant (never an ancestor).
 * Runs in the page, synchronously, so it can sit immediately before the press.
 *
 * This is separate from the stability wait because a page can reflow *after* the box last held
 * still — an in-flow autocomplete that opens a moment later pushes the target down and puts itself
 * under the old point. Re-measuring here, at the last instant, is what catches that.
 */
export function hitTestTargetInPage(query: MeasureQuery): MeasureResult {
  const MARKER = "data-pl-target";
  // PL-018b: a click is dispatched in TOP-viewport coordinates, but this document measures in its
  // own viewport. In a same-origin frame, each enclosing iframe's content-box origin is added on the
  // way up. A cross-origin parent hides `frameElement` (it reads null), so no offset is known and
  // the coordinates are not the top viewport's: `topFrame` is then false and a click is refused.
  const frameOffset = (): { dx: number; dy: number } | null => {
    let dx = 0;
    let dy = 0;
    try {
      let win: Window = window;
      while (win !== win.top) {
        const host = win.frameElement as HTMLElement | null;
        if (!host) return null;
        const box = host.getBoundingClientRect();
        const style = win.parent.getComputedStyle(host);
        dx += box.left + host.clientLeft + (parseFloat(style.paddingLeft) || 0);
        dy += box.top + host.clientTop + (parseFloat(style.paddingTop) || 0);
        win = win.parent;
      }
    } catch {
      return null;
    }
    return { dx, dy };
  };
  const topFrame = frameOffset() !== null;
  const toTop = (x: number, y: number): { x: number; y: number } => {
    const offset = frameOffset() ?? { dx: 0, dy: 0 };
    return { x: x + offset.dx, y: y + offset.dy };
  };
  const reject = (reason: string): MeasureResult => ({
    ok: false,
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    hit: "",
    topFrame,
    reason,
  });

  // The marked element can live inside an OPEN shadow root, which `document.querySelector` cannot
  // see past. The light tree is searched first — the same element a plain document query finds, and
  // an invalid token still throws there — then each host's open shadow root, in document order. A
  // closed root is invisible (`el.shadowRoot` is null), so nothing inside it is found.
  const deepMarker = (selector: string): Element | null => {
    const light = document.querySelector(selector);
    if (light) return light;
    const roots: ShadowRoot[] = [];
    const collect = (root: Document | ShadowRoot): void => {
      for (const el of Array.from(root.querySelectorAll("*"))) {
        const shadow = el.shadowRoot;
        if (shadow) {
          roots.push(shadow);
          collect(shadow);
        }
      }
    };
    collect(document);
    for (const shadow of roots) {
      const found = shadow.querySelector(selector);
      if (found) return found;
    }
    return null;
  };

  let element: Element | null = null;
  try {
    const escaped = String(query.token).replace(/["\\]/g, "\\$&");
    element = deepMarker(`[${MARKER}="${escaped}"]`);
  } catch {
    return reject("the marker token is not a valid selector");
  }
  if (!element) return reject("the marked element is no longer in the document");

  const rect = element.getBoundingClientRect();
  const width = rect.width;
  const height = rect.height;
  if (!(width > 0) || !(height > 0)) {
    return reject(`the element has no clickable area (${width}x${height})`);
  }
  const x = rect.left + width / 2;
  const y = rect.top + height / 2;
  const fromPoint =
    typeof document.elementFromPoint === "function" ? document.elementFromPoint : null;
  if (!fromPoint) {
    // jsdom has no elementFromPoint; in a browser it is present, so the check is real wherever a
    // click can actually be dispatched.
    return { ok: true, ...toTop(x, y), width, height, hit: "", topFrame };
  }
  const hit = fromPoint.call(document, x, y);
  // A shadow target reports its host here, because the point lookup retargets across the shadow
  // boundary. Walking the host chain (through `parentElement`/`parentNode`; a shadow root needs
  // no `getRootNode`) accepts that host as the target's own tree, while an ordinary wrapper that
  // merely contains the target is still refused.
  const hostChain: Element[] = [];
  let node: Element | null = element;
  while (node) {
    const parent: Element | null = node.parentElement;
    if (parent) {
      node = parent;
      continue;
    }
    const rootNode: Node | null = node.parentNode;
    const hostEl: Element | null =
      rootNode && (rootNode as ShadowRoot).host ? (rootNode as ShadowRoot).host : null;
    if (!hostEl) break;
    hostChain.push(hostEl);
    node = hostEl;
  }
  if (hit && (hit === element || element.contains(hit) || hostChain.includes(hit))) {
    // In a frame, the point must also reach this frame in every enclosing document: an overlay in
    // the parent page over the iframe would take the click even though the frame's own test passed.
    let win: Window = window;
    let px = x;
    let py = y;
    try {
      while (win !== win.top) {
        const host = win.frameElement as HTMLElement | null;
        if (!host) break;
        const box = host.getBoundingClientRect();
        const style = win.parent.getComputedStyle(host);
        px += box.left + host.clientLeft + (parseFloat(style.paddingLeft) || 0);
        py += box.top + host.clientTop + (parseFloat(style.paddingTop) || 0);
        const above = win.parent.document.elementFromPoint(px, py);
        if (above !== host) {
          return reject(
            `the target is covered by another element (the frame is covered by <${above ? above.tagName.toLowerCase() : "nothing"}> in its parent page)`,
          );
        }
        win = win.parent;
      }
    } catch {
      return reject("the frame's parent page could not be read");
    }
    return { ok: true, ...toTop(x, y), width, height, hit: hit.tagName.toLowerCase(), topFrame };
  }
  return reject(
    hit
      ? `the target is covered by another element (the box center is covered by <${hit.tagName.toLowerCase()}>)`
      : "the target is covered by another element (the hit test found no element at the box center)",
  );
}

/**
 * Find the element a target ladder describes. Runs in the page.
 *
 * `query.token` is stamped on the found element as `data-pl-target="<token>"`, so
 * every later CDP call (focus, clear, read) can address the same element without
 * re-climbing the ladder.
 */
export function resolveTargetInPage(query: ResolveQuery): ResolveResult {
  const LADDER = ["role", "testid", "label", "css", "text_fuzzy"];
  const MARKER = "data-pl-target";
  const attempts: RungAttempt[] = [];

  const norm = (value: unknown): string =>
    value === null || value === undefined ? "" : String(value).replace(/\s+/g, " ").trim();
  const lower = (value: unknown): string => norm(value).toLowerCase();

  const doc = document;

  // PL-107: the accessible name skips what the page hides, exactly as the recorder's
  // `nodeTextExcludingHelp` does: aria-hidden, `hidden`, and display:none/visibility:hidden. The
  // resolver is serialized into the page, so the rule lives here, self-contained.
  const textOf = (el: Element): string => {
    const pieces: string[] = [];
    const view = doc.defaultView;
    const computed =
      view && typeof view.getComputedStyle === "function"
        ? view.getComputedStyle.bind(view)
        : null;
    const hiddenStyle = (node: Element): boolean => {
      if (!computed) return false;
      try {
        const style = computed(node);
        return style.display === "none" || style.visibility === "hidden";
      } catch {
        return false;
      }
    };
    const walk = (node: Node, skip: boolean): void => {
      if (node.nodeType === 3) {
        if (!skip) pieces.push(node.textContent ?? "");
        return;
      }
      if (node.nodeType !== 1) return;
      const child = node as Element;
      const inline = (child as HTMLElement).style;
      const hidden =
        child.getAttribute("aria-hidden") === "true" ||
        child.hasAttribute("hidden") ||
        (inline && (inline.display === "none" || inline.visibility === "hidden")) ||
        hiddenStyle(child);
      const childSkip = skip || hidden;
      for (const grandchild of Array.from(child.childNodes)) walk(grandchild, childSkip);
    };
    for (const child of Array.from(el.childNodes)) walk(child, false);
    return norm(pieces.join(""));
  };

  // A shadow root is a separate tree: `querySelector`, `querySelectorAll` and `getElementById` stop
  // at the boundary, so a control inside `attachShadow({ mode: "open" })` is unreachable by a plain
  // document query. These helpers walk into every OPEN shadow root, in document order — a shadow
  // tree is visited where its host appears — so a light-DOM-only page yields exactly the same
  // elements in exactly the same order as before. A CLOSED root is not visible to page script
  // (`el.shadowRoot` is null), so nothing inside one is ever returned.
  const deepElements = (root: Document | ShadowRoot | Element): Element[] => {
    const out: Element[] = [];
    const walk = (node: Document | ShadowRoot | Element): void => {
      for (const el of Array.from(node.querySelectorAll("*"))) {
        out.push(el);
        const shadow = el.shadowRoot;
        if (shadow) walk(shadow);
      }
    };
    walk(root);
    return out;
  };

  /** The matches of `selector` under `root`, open shadow roots included, in the same order. */
  const deepQuery = (root: Document | ShadowRoot | Element, selector: string): Element[] =>
    deepElements(root).filter((el) => el.matches(selector));

  /** The first match of `selector` under `root`, open shadow roots included. */
  const deepFirst = (root: Document | ShadowRoot | Element, selector: string): Element | null =>
    deepQuery(root, selector)[0] ?? null;

  /** The first element with this id under `root`, open shadow roots included. */
  const deepFirstId = (root: Document | ShadowRoot | Element, id: string): Element | null =>
    deepElements(root).find((el) => el.id === id) ?? null;

  /**
   * The trees an element can be labelled from: its own root — an open shadow root, when it lives in
   * one — and then the document. A light-DOM element's own root IS the document, so a page without
   * shadow roots resolves through exactly the query it always did.
   */
  const trees = (el: Element): Array<Document | ShadowRoot | Element> => {
    const own = (el.getRootNode ? el.getRootNode() : doc) as Document | ShadowRoot | Element;
    return !own || own === doc ? [doc] : [own, doc];
  };

  /** The first hit of `finder` across the trees an element can be labelled from. */
  const findIn = (
    el: Element,
    finder: (root: Document | ShadowRoot | Element) => Element | null,
  ): Element | null => {
    for (const root of trees(el)) {
      const found = finder(root);
      if (found) return found;
    }
    return null;
  };

  /**
   * Whether the accessible name is the element's own text content: no higher-precedence source
   * (aria-labelledby, aria-label, a label, a field's value/placeholder/title, alt, title) names it.
   * Only such an element can carry the old full-text name a recipe was pinned with (PL-107).
   */
  const nameIsOwnText = (el: Element): boolean => {
    if (norm(el.getAttribute("aria-labelledby"))) return false;
    if (norm(el.getAttribute("aria-label"))) return false;
    if (labelTextOf(el)) return false;
    const tag = el.tagName.toUpperCase();
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return false;
    if (norm(el.getAttribute("alt"))) return false;
    if (norm(el.getAttribute("title"))) return false;
    if (el.getAttribute("contenteditable") === "true") return false;
    return true;
  };

  const roleOf = (el: Element): string => {
    // This must agree with `semanticRole` in shared/fingerprint.ts, which spells the rung: a
    // recorded role is the role the resolver must read (PL-048). That side skips role="label"
    // (the recorder never emits it -- a click on a label is recorded against the control the
    // label names) and any role that could not be a recipe rung, falling back to the implicit
    // role of the tag; so does this.
    const explicit = norm(el.getAttribute("role"));
    if (explicit) {
      const first = explicit.split(" ")[0];
      if (first) {
        const role = first.toLowerCase();
        if (role !== "label" && /^[a-z][a-z0-9-]*$/.test(role)) return role;
      }
    }
    const tag = el.tagName.toUpperCase();
    if (tag === "INPUT") {
      const type = (el.getAttribute("type") ?? "text").toLowerCase();
      const map: Record<string, string> = {
        button: "button",
        submit: "button",
        reset: "button",
        image: "button",
        checkbox: "checkbox",
        radio: "radio",
        range: "slider",
        number: "spinbutton",
        search: "searchbox",
        email: "textbox",
        tel: "textbox",
        text: "textbox",
        url: "textbox",
        password: "textbox",
        file: "button",
      };
      return map[type] ?? "textbox";
    }
    const tags: Record<string, string> = {
      A: "link",
      BUTTON: "button",
      SELECT: "combobox",
      TEXTAREA: "textbox",
      IMG: "img",
      TABLE: "table",
      UL: "list",
      OL: "list",
      LI: "listitem",
      NAV: "navigation",
      MAIN: "main",
      HEADER: "banner",
      FOOTER: "contentinfo",
      FORM: "form",
      DIALOG: "dialog",
      FIELDSET: "group",
      OPTION: "option",
      // Deliberately absent: LABEL. `label` is a tag, not a role; the recorder records a click
      // on a label against the control the label names and never emits `role: "label"`, so a
      // label is `generic` here exactly as it is there.
      // The table trio, which is what a results table is made of. A search result is a cell, and
      // rung 1 -- role plus accessible name -- is the rung a recipe should be able to use for one.
      // Without them a cell is `generic` and the only rung left is a CSS selector into someone
      // else's markup, which is precisely the durability the ladder exists to avoid.
      TD: "cell",
      TH: "columnheader",
      TR: "row",
      H1: "heading",
      H2: "heading",
      H3: "heading",
      H4: "heading",
      H5: "heading",
      H6: "heading",
    };
    if (tag === "A" && !el.hasAttribute("href")) return "generic";
    return tags[tag] ?? "generic";
  };

  const labelTextOf = (el: Element): string => {
    const id = el.getAttribute("id");
    if (id) {
      const escaped = id.replace(/["\\]/g, "\\$&");
      // The label can sit in the same open shadow root as the control it names, which a document
      // query cannot see past; the control's own root is searched before the document.
      const forLabel = findIn(el, (root) => deepFirst(root, `label[for="${escaped}"]`));
      if (forLabel) {
        const text = textOf(forLabel);
        if (text) return text;
      }
    }
    const wrapping = el.closest("label");
    return wrapping ? textOf(wrapping) : "";
  };

  const accessibleNameOf = (el: Element): string => {
    const labelledby = norm(el.getAttribute("aria-labelledby"));
    if (labelledby) {
      const text = norm(
        labelledby
          .split(" ")
          .map((id) => {
            const target = findIn(el, (root) => deepFirstId(root, id));
            return target ? textOf(target) : "";
          })
          .join(" "),
      );
      if (text) return text;
    }
    const ariaLabel = norm(el.getAttribute("aria-label"));
    if (ariaLabel) return ariaLabel;
    const label = labelTextOf(el);
    if (label) return label;
    const tag = el.tagName.toUpperCase();
    if (tag === "INPUT") {
      const type = (el.getAttribute("type") ?? "text").toLowerCase();
      if (type === "submit" || type === "button" || type === "reset") {
        return norm((el as HTMLInputElement).value);
      }
      const placeholder = norm(el.getAttribute("placeholder"));
      if (placeholder) return placeholder;
      return norm(el.getAttribute("title"));
    }
    if (tag === "SELECT" || tag === "TEXTAREA") {
      const placeholder = norm(el.getAttribute("placeholder"));
      if (placeholder) return placeholder;
      return norm(el.getAttribute("title"));
    }
    const alt = norm(el.getAttribute("alt"));
    if (alt) return alt;
    // The author's own `title` comes before the element's text, exactly as the recorder's
    // `accessibleName` computes it: the text may be a glyph (the ✕ on a chip), and the two
    // sides must agree or a recorded role rung can never match.
    const title = norm(el.getAttribute("title"));
    if (title) return title;
    if (el.getAttribute("contenteditable") === "true") return "";
    return textOf(el);
  };

  /**
   * The name of the column header a table cell belongs to, or "" when the element does not
   * sit in one: the `th` (or `role=columnheader`) at the cell's column, counting `colspan`
   * (`aria-colspan` in a grid) or matched by `aria-colindex`, its text trimmed with sort
   * arrows and icon glyphs left out.
   *
   * This mirrors `columnHeaderName` in shared/fingerprint.ts (PL-048): the resolver is
   * serialized into the page as a self-contained function and cannot import the recorder's
   * code, so the rule lives here too, and tests/order-line-names.test.ts runs the same
   * elements through both sides to pin the agreement. A role rung named by the recorder's
   * header name must resolve here.
   */
  const HEADER_GLYPH_SELECTOR =
    "i, svg, [aria-hidden='true'], .o_list_sortable_caret, .o_resize, .o_list_header_label_spacer";
  const CELL_ROLES = ["cell", "gridcell"];
  const HEADER_CELL_ROLES = ["columnheader"];
  const indexAttrOf = (el: Element, name: string): number | null => {
    const raw = norm(el.getAttribute(name));
    return /^\d+$/.test(raw) ? parseInt(raw, 10) : null;
  };
  const isCellLike = (el: Element): boolean =>
    el.tagName.toUpperCase() === "TD" ||
    CELL_ROLES.includes((el.getAttribute("role") ?? "").trim().toLowerCase());
  const isHeaderCellLike = (el: Element): boolean =>
    el.tagName.toUpperCase() === "TH" ||
    HEADER_CELL_ROLES.includes((el.getAttribute("role") ?? "").trim().toLowerCase());
  const columnHeaderNameOf = (el: Element): string => {
    const cell =
      el.parentElement && isCellLike(el)
        ? el
        : el.closest?.("td, [role='cell'], [role='gridcell']");
    if (!cell) return "";
    const table = cell.closest?.("table, [role='table'], [role='grid']");
    if (!table) return "";

    let column: number | null = null;
    const colIndex = indexAttrOf(cell, "aria-colindex");
    if (colIndex !== null) {
      column = colIndex - 1;
    } else {
      const row = cell.parentElement;
      if (!row) return "";
      const cells = Array.from(row.children).filter(isCellLike);
      column = cells.indexOf(cell);
    }
    if (column === null || column < 0) return "";

    const thead = table.querySelector("thead");
    const headerRow =
      thead?.children[0] ??
      (table.tagName.toUpperCase() === "TR" ? table : table.querySelector("tr, [role='row']"));
    if (!headerRow) return "";

    let cursor = 0;
    let header: Element | null = null;
    for (const candidate of Array.from(headerRow.children).filter(isHeaderCellLike)) {
      const own = indexAttrOf(candidate, "aria-colindex");
      const spanValue = indexAttrOf(candidate, "aria-colspan");
      const start = own !== null ? own - 1 : cursor;
      const span = Math.max(
        1,
        own !== null ? (spanValue ?? 1) : (indexAttrOf(candidate, "colspan") ?? 1),
      );
      if (column >= start && column < start + span) {
        header = candidate;
        break;
      }
      cursor = start + span;
    }
    if (!header) return "";

    const clone = header.cloneNode(true) as Element;
    for (const glyph of Array.from(clone.querySelectorAll(HEADER_GLYPH_SELECTOR))) {
      glyph.remove();
    }
    return norm(clone.textContent);
  };

  // The fuzzy rung reads a *label*, never a value: an input's current text is
  // what it happens to hold, not what it is called. Matching on it would let a
  // value typed earlier in the run re-target a later step.
  const fuzzyTextOf = (el: Element): string =>
    norm(el.getAttribute("placeholder")) ||
    norm(el.getAttribute("aria-label")) ||
    norm(el.getAttribute("title")) ||
    norm(el.textContent);

  const isDisabled = (el: Element): boolean => {
    const node = el as HTMLInputElement;
    if (node.disabled === true) return true;
    if (lower(el.getAttribute("aria-disabled")) === "true") return true;
    // PL-107 item 11: a control the page is busy with is not ready to act on either; the click
    // path waits for it, and the step's own assertion may show the page already moved on.
    return lower(el.getAttribute("aria-busy")) === "true";
  };

  const isVisible = (el: Element): boolean => {
    if (el.hasAttribute("hidden")) return false;
    const view = doc.defaultView;
    const style = view?.getComputedStyle ? view.getComputedStyle(el) : null;
    if (style && (style.display === "none" || style.visibility === "hidden")) return false;
    // An element inside a hidden ancestor also fails the style test above only
    // for itself; walk up for the common `hidden`/display:none case.
    let parent: Element | null = el.parentElement;
    while (parent) {
      if (parent.hasAttribute("hidden")) return false;
      parent = parent.parentElement;
    }
    return true;
  };

  const isUsable = (el: Element): boolean => isVisible(el) && !isDisabled(el);

  const inScope = (el: Element, scope: Element | null): boolean =>
    scope === null || scope.contains(el);

  const elements = (scope: Element | null): Element[] => deepElements(scope ?? doc);

  /**
   * Resolve a "kind:Name" container descriptor to exactly one *visible*
   * element. A descriptor that names two visible containers is a refusal, not a
   * guess: scoping a rung to the wrong form would silently act on the wrong
   * part of the page. The reason is reported so the rung can be skipped.
   */
  const container = (descriptor: string): { el: Element | null; reason?: string } => {
    const trimmed = norm(descriptor);
    if (!trimmed) return { el: null };
    const colon = trimmed.indexOf(":");
    const kind = lower(colon === -1 ? trimmed : trimmed.slice(0, colon));
    const name = norm(colon === -1 ? "" : trimmed.slice(colon + 1));
    const named = elements(null).filter((el) => {
      const tag = el.tagName.toLowerCase();
      const explicitRole = lower(el.getAttribute("role"));
      if (tag !== kind && explicitRole !== kind && roleOf(el) !== kind) return false;
      // The name is the container's accessible name, or its id. A control that
      // names its own listbox (`aria-controls="dd-list"`) is scoped with
      // `listbox:dd-list`, and a listbox usually carries no accessible name of
      // its own — its text is its options' text.
      if (name && lower(accessibleNameOf(el)) !== lower(name) && el.id !== name) return false;
      return true;
    });
    const visible = named.filter(isVisible);
    if (visible.length === 0) return { el: null };
    if (visible.length > 1) {
      return { el: null, reason: `container "${trimmed}" is not unique` };
    }
    return { el: visible[0] as Element };
  };

  const fuzzyScore = (haystack: string, needle: string): number => {
    const text = lower(haystack);
    const target = lower(needle);
    if (!text || !target) return 0;
    if (text === target) return 1;
    if (!text.includes(target)) return 0;
    return target.length / text.length;
  };

  const stamp = (el: Element, token: string): void => {
    // Stale markers are cleared wherever they are, an open shadow root included: a marker left
    // behind inside one would otherwise be found by a deep lookup before the new target.
    for (const marked of deepQuery(doc, `[${MARKER}]`)) {
      marked.removeAttribute(MARKER);
    }
    el.setAttribute(MARKER, token);
  };

  const fail = (reason: string): ResolveResult => ({
    ok: false,
    rung: -1,
    by: "",
    marker: null,
    x: 0,
    y: 0,
    tag: "",
    attempts,
    // The page the ladder climbed, and how much of it there is. A step that cannot resolve its
    // target on a page the person can see is a question about *which* page, and a count is the
    // cheapest way to tell "the element is gone" from "the document is not the one expected"
    // (PL-027: the trail showed three rungs with no match, and this is what says why).
    error: `${reason} [page ${locate()}; ${doc.querySelectorAll("input").length} input(s), ${doc.querySelectorAll("*").length} element(s)]`,
  });

  /** The top-level URL the ladder is climbing in, as the page itself reports it. */
  const locate = (): string => {
    try {
      return String(doc.location?.href ?? "unknown");
    } catch {
      return "unreadable";
    }
  };

  const requested = (query.targets ?? []) as ResolveRung[];
  if (requested.length === 0) return fail("the step has no target ladder");

  const rankOf = (by: unknown): number => LADDER.indexOf(lower(by));
  // Climb in rung RANK order, not the order the recipe happened to list: a
  // recipe that puts `css` before `role` must still try `role` first. The sort
  // is stable, so two rungs of the same rank keep their recipe order, and a rung
  // the format does not define sorts last.
  const targets = requested
    .map((target, index) => ({ target, index }))
    .sort((a, b) => rankOf(a.target.by) - rankOf(b.target.by) || a.index - b.index)
    .map((entry) => entry.target);

  for (let rung = 0; rung < targets.length; rung += 1) {
    const target = targets[rung] as ResolveRung;
    const by = norm(target.by);
    const rank = rankOf(by);
    // Rungs are reported as the recipe format numbers them, 1..5; 0 marks a rung
    // that is not in the ladder at all.
    const rungNumber = rank === -1 ? 0 : rank + 1;
    const scopeDescriptor = norm(target.within) || norm(query.within);
    const containerResult = scopeDescriptor
      ? container(scopeDescriptor)
      : { el: null as Element | null, reason: undefined as string | undefined };
    const scope = containerResult.el;

    if (scopeDescriptor && !scope) {
      attempts.push({
        rung: rungNumber,
        by,
        matches: 0,
        reason: containerResult.reason ?? `container "${scopeDescriptor}" not found`,
      });
      continue;
    }

    let candidates: Element[] = [];

    if (by === "role") {
      const role = lower(target.role);
      const name = norm(target.name);
      candidates = elements(scope).filter(
        (el) =>
          inScope(el, scope) &&
          roleOf(el) === role &&
          // The rung's name matches the element's accessible name, or the name of the column
          // header its cell belongs to (PL-048). The recorder names an unlabeled row field by
          // its header BEFORE the placeholder, so the recorded rung says "Product" where the
          // element's own accessible name is the placeholder "Search a product"; accepting
          // either keeps recipes pinned with placeholder names resolving.
          (name === "" ||
            lower(accessibleNameOf(el)) === lower(name) ||
            // PL-107: a recipe saved before hidden subtrees were left out of the name carried the
            // old doubled name ("ContinueContinuing"); when the name is the element's own text, the
            // full text still matches it. An element with a higher-precedence name (aria-label,
            // label, title) keeps that name, so it gains no second one.
            (nameIsOwnText(el) && lower(norm(el.textContent)) === lower(name)) ||
            lower(columnHeaderNameOf(el)) === lower(name) ||
            // PL-099: a list that shows its entries as "[CODE] Name" (Odoo's products) is
            // answered by a spreadsheet that holds the name alone. The whole name must still
            // match: "Desk Lamp" never picks "[LMP-1] Desk Lamp Pro".
            (role === "option" &&
              lower(accessibleNameOf(el).replace(/^\s*\[[^\]]*\]\s*/, "")) === lower(name))),
      );
    } else if (by === "testid") {
      const value = norm(target.value);
      candidates = deepQuery(scope ?? doc, "[data-testid], [data-test]").filter((el) => {
        const id = norm(el.getAttribute("data-testid") ?? el.getAttribute("data-test"));
        return id === value;
      });
    } else if (by === "label") {
      const text = lower(target.text);
      const found: Element[] = [];
      for (const label of deepQuery(scope ?? doc, "label")) {
        if (lower(norm(label.textContent)) !== text) continue;
        const id = label.getAttribute("for");
        const control = id
          ? findIn(label, (root) => deepFirstId(root, id))
          : deepFirst(label, "input, select, textarea");
        if (control) found.push(control);
      }
      candidates = found;
    } else if (by === "css") {
      const selector = norm(target.selector);
      try {
        // Collected deep, so a selector naming an element inside an open shadow root — where a
        // document-level query stops — still resolves to it.
        candidates = deepQuery(scope ?? doc, selector);
      } catch {
        attempts.push({ rung: rungNumber, by, matches: 0, reason: `invalid selector ${selector}` });
        continue;
      }
    } else if (by === "text_fuzzy") {
      const value = norm(target.value);
      const threshold = typeof target.threshold === "number" ? target.threshold : 0.6;
      const all = elements(scope);
      const matches = all.filter(
        (el) => inScope(el, scope) && fuzzyScore(fuzzyTextOf(el), value) >= threshold,
      );
      // Prefer the deepest match, so a wrapper that merely contains the text is
      // not chosen over the element that actually carries it.
      candidates = matches.filter(
        (el) => !matches.some((other) => other !== el && el.contains(other)),
      );
    } else {
      attempts.push({ rung: rungNumber, by, matches: 0, reason: `unknown rung ${by}` });
      continue;
    }

    const usable = candidates.filter(isUsable);
    if (usable.length === 1) {
      const element = usable[0] as Element;
      stamp(element, query.token);
      const box = measureTargetInPage({ token: query.token });
      attempts.push({ rung: rungNumber, by, matches: 1 });
      return {
        ok: true,
        rung: rungNumber,
        by,
        marker: query.token,
        x: box.x,
        y: box.y,
        tag: element.tagName.toLowerCase(),
        name: accessibleNameOf(element),
        role: roleOf(element),
        attempts,
      };
    }

    attempts.push({
      rung: rungNumber,
      by,
      matches: usable.length,
      // How many the rung *matched*, before visibility and enabledness: a rung that found the
      // element and refused it is a different problem from one that found nothing, and the two
      // look identical in the count above.
      found: candidates.length,
      reason: usable.length === 0 ? "no visible, enabled match" : "more than one match",
    });
  }

  return fail("no rung of the target ladder resolved to exactly one element");
}

/**
 * The resolver as a source string, for injection into a page context.
 *
 * The resolver calls the measurement function, so both are injected together:
 * the composed expression evaluates to the resolver, with the measurement in its
 * enclosing scope. A stamp happens before measuring, so the measurement finds
 * the element by its marker.
 */
export function resolverExpression(): string {
  return `(() => { const measureTargetInPage = ${measureTargetInPage.toString()}; return (${resolveTargetInPage.toString()}); })()`;
}

/** The measurement function as an injectable expression. */
export function measureExpression(): string {
  return `(${measureTargetInPage.toString()})`;
}

/** The settle function as an injectable expression. */
export function settleExpression(): string {
  return `(${settleTargetInPage.toString()})`;
}

/** The hit-test function as an injectable expression. */
export function hitTestExpression(): string {
  return `(${hitTestTargetInPage.toString()})`;
}

/** An ES-module-safe name for the resolver, used in tests. */
export const resolveTargetSource = resolverExpression;
