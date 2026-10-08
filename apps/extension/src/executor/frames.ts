// Frame support: finding the execution context a step's `frame` path points at.
//
// A recipe stores a frame path as a chain of iframes from the top document down
// (by position, or by URL pattern). The executor walks the frame tree the page
// reports and picks the matching frame, then runs that step's page calls in the
// frame's execution context.
//
// Known limitation, reported rather than hidden: CDP mouse coordinates for a
// non-top frame are viewport coordinates, while the resolver measures a box in
// its own frame. Clicking inside a nested frame is therefore best-effort; the
// executor prefers element-level dispatch (focus/clear/select) inside frames,
// and a click in a nested frame is verified by the step's own assertion.
import type { FrameStep } from "../shared/recipe-types";
import type { CdpTransport } from "./cdp";

export interface FrameNode {
  frameId: string;
  url: string;
  children: FrameNode[];
}

interface RawFrameTree {
  frame?: { id?: string; url?: string };
  childFrames?: RawFrameTree[];
}

interface FrameTreePayload {
  frameTree?: RawFrameTree;
}

function toNode(payload: RawFrameTree | undefined): FrameNode | null {
  const frame = payload?.frame;
  if (!frame?.id) return null;
  return {
    frameId: frame.id,
    url: frame.url ?? "",
    children: (payload?.childFrames ?? [])
      .map((child) => toNode(child))
      .filter((node): node is FrameNode => node !== null),
  };
}

/**
 * Walk a frame path from the root, matching each step by position or URL.
 * Returns null when the path does not resolve.
 */
export function selectFrame(root: FrameNode, path: FrameStep[]): FrameNode | null {
  let current = root;
  for (const step of path) {
    const record = step as { by?: string; index?: number; url_matches?: string };
    let next: FrameNode | undefined;
    if (record.by === "index") {
      next = current.children[record.index ?? -1];
    } else if (record.by === "url") {
      const pattern = record.url_matches ?? "";
      let matcher: RegExp | null = null;
      try {
        matcher = new RegExp(pattern);
      } catch {
        matcher = null;
      }
      next = current.children.find(
        (child) => (matcher ? matcher.test(child.url) : false),
      );
    }
    if (!next) return null;
    current = next;
  }
  return current;
}

/**
 * The execution contexts the page has reported, keyed by frame. The executor
 * needs a frame's context id to run `Runtime.evaluate` inside it.
 */
export class FrameContexts {
  private readonly byFrame = new Map<string, number>();
  private unsubscribe: (() => void) | null = null;

  /** Start listening to `Runtime.executionContextCreated`. Idempotent. */
  listen(cdp: CdpTransport): void {
    if (this.unsubscribe || !cdp.on) return;
    this.unsubscribe = cdp.on("Runtime.executionContextCreated", (params) => {
      const context = params["context"] as
        | { id?: number; auxData?: { frameId?: string; isDefault?: boolean } }
        | undefined;
      const frameId = context?.auxData?.frameId;
      if (frameId && typeof context?.id === "number" && context.auxData?.isDefault) {
        this.byFrame.set(frameId, context.id);
      }
    });
  }

  /** The default execution context of a frame, once the page has reported it. */
  contextIdFor(frameId: string): number | undefined {
    return this.byFrame.get(frameId);
  }

  /** Whether a frame's context has been seen. */
  knows(frameId: string): boolean {
    return this.byFrame.has(frameId);
  }

  stop(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
  }
}

/** Read the page's frame tree and return its root. */
export async function readFrameTree(cdp: CdpTransport): Promise<FrameNode | null> {
  const result = (await cdp.send("Page.getFrameTree")) as FrameTreePayload;
  return toNode(result.frameTree);
}
