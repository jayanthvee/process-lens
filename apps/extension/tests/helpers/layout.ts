// A small layout stub for jsdom, which has no layout engine.
//
// The executor measures an element's box, waits for it to hold still across two
// animation frames, and hit-tests the center before a click. jsdom returns a
// zero-size rect for everything, has no `elementFromPoint`, and may not run
// animation frames, so tests that exercise the click path install this stub: a
// non-zero rect (fixed, or a scripted sequence to model a reflow), a hit test that
// answers with the marked element (or a miss, a stranger), and a real
// `requestAnimationFrame` driven by a macrotask, so a frame always arrives.
// Production code is never weakened for the tests.
export interface LayoutBox {
  left?: number;
  top?: number;
  width?: number;
  height?: number;
}

export interface LayoutOptions {
  width?: number;
  height?: number;
  left?: number;
  top?: number;
  /** What `elementFromPoint` returns: the marked element, nothing, or a stranger. */
  hit?: "target" | "none" | "other";
  /**
   * The box returned by the nth `getBoundingClientRect()` call across the whole document. Lets a
   * test model a page that moves once and settles (two boxes) or never settles (an alternation).
   * When absent, every call returns the fixed rect built from `left/top/width/height`.
   */
  rectAt?: (call: number, element: Element) => LayoutBox;
  /** What `elementFromPoint` returns for the nth call; overrides `hit` when present. */
  hitAt?: (call: number, x: number, y: number) => Element | null;
}

export function installLayout(options: LayoutOptions = {}): () => void {
  const width = options.width ?? 120;
  const height = options.height ?? 24;
  const left = options.left ?? 10;
  const top = options.top ?? 10;

  const fixed: Required<LayoutBox> = { left, top, width, height };
  let rectCalls = 0;
  let hitCalls = 0;

  const originalRect = Element.prototype.getBoundingClientRect;
  const originalFromPoint = Document.prototype.elementFromPoint;
  const originalRaf = globalThis.requestAnimationFrame;
  const originalCancelRaf = globalThis.cancelAnimationFrame;
  const hadRaf = "requestAnimationFrame" in globalThis;

  Element.prototype.getBoundingClientRect = function getBoundingClientRect() {
    rectCalls += 1;
    const box = options.rectAt ? options.rectAt(rectCalls - 1, this) : fixed;
    const w = box.width ?? width;
    const h = box.height ?? height;
    const l = box.left ?? left;
    const t = box.top ?? top;
    return {
      x: l,
      y: t,
      left: l,
      top: t,
      right: l + w,
      bottom: t + h,
      width: w,
      height: h,
      toJSON: () => ({}),
    } as DOMRect;
  };

  Document.prototype.elementFromPoint = function elementFromPoint(x: number, y: number) {
    const call = hitCalls;
    hitCalls += 1;
    if (options.hitAt) return options.hitAt(call, x, y);
    if (options.hit === "none") return null;
    if (options.hit === "other") return document.createElement("div") as Element;
    return document.querySelector("[data-pl-target]");
  } as typeof Document.prototype.elementFromPoint;

  // A deterministic animation frame: jsdom's own may never fire without `pretendToBeVisual`, and a
  // settled box must be measurable. It is a macrotask, so real timers still order it.
  globalThis.requestAnimationFrame = ((cb: FrameRequestCallback) =>
    setTimeout(() => cb(Date.now()), 0) as unknown as number) as typeof requestAnimationFrame;
  globalThis.cancelAnimationFrame = ((id: number) => clearTimeout(id)) as typeof cancelAnimationFrame;

  return () => {
    Element.prototype.getBoundingClientRect = originalRect;
    Document.prototype.elementFromPoint = originalFromPoint;
    if (hadRaf) {
      globalThis.requestAnimationFrame = originalRaf;
      globalThis.cancelAnimationFrame = originalCancelRaf;
    } else {
      delete (globalThis as { requestAnimationFrame?: unknown }).requestAnimationFrame;
      delete (globalThis as { cancelAnimationFrame?: unknown }).cancelAnimationFrame;
    }
  };
}
