// A small layout stub for jsdom, which has no layout engine.
//
// The executor measures an element's box and hit-tests its center before a
// click. jsdom returns a zero-size rect for everything and has no
// `elementFromPoint`, so tests that exercise the click path install this stub:
// a non-zero rect, and a hit test that answers with the marked element (or a
// miss, when asked). Production code is never weakened for the tests.
export interface LayoutOptions {
  width?: number;
  height?: number;
  left?: number;
  top?: number;
  /** What `elementFromPoint` returns: the marked element, nothing, or a stranger. */
  hit?: "target" | "none" | "other";
}

export function installLayout(options: LayoutOptions = {}): () => void {
  const width = options.width ?? 120;
  const height = options.height ?? 24;
  const left = options.left ?? 10;
  const top = options.top ?? 10;

  const rect = {
    x: left,
    y: top,
    left,
    top,
    right: left + width,
    bottom: top + height,
    width,
    height,
    toJSON: () => ({}),
  } as DOMRect;

  const originalRect = Element.prototype.getBoundingClientRect;
  const originalFromPoint = Document.prototype.elementFromPoint;

  Element.prototype.getBoundingClientRect = function getBoundingClientRect() {
    return rect;
  };

  Document.prototype.elementFromPoint = function elementFromPoint() {
    if (options.hit === "none") return null;
    if (options.hit === "other") return document.createElement("div") as Element;
    return document.querySelector("[data-pl-target]");
  } as typeof Document.prototype.elementFromPoint;

  return () => {
    Element.prototype.getBoundingClientRect = originalRect;
    Document.prototype.elementFromPoint = originalFromPoint;
  };
}
