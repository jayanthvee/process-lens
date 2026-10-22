/**
 * PL-087: this extension install's browser id.
 *
 * Made once, at random, and kept in the extension's own storage. The hub learns it with every
 * session, and the dashboard tab in the same Chrome learns it from the content script, so a run
 * started from that tab is driven by this Chrome and nobody else's. It is a routing label, not a
 * credential: it proves nothing and unlocks nothing.
 */

export const BROWSER_ID_KEY = "processlens.browserId";
/** The ProcessLens address this extension last connected to: the only page told the id. */
export const LAST_HUB_KEY = "processlens.lastHub";

export interface LocalStore {
  get(keys: string[]): Promise<Record<string, unknown>>;
  set(items: Record<string, unknown>): Promise<void>;
}

const SHAPE = /^[A-Za-z0-9_-]{8,64}$/;

function freshId(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The stored id, made and stored the first time it is asked for. */
export async function browserId(store: LocalStore | undefined): Promise<string> {
  if (!store) return "";
  const stored = (await store.get([BROWSER_ID_KEY]))[BROWSER_ID_KEY];
  if (typeof stored === "string" && SHAPE.test(stored)) return stored;
  const made = freshId();
  await store.set({ [BROWSER_ID_KEY]: made });
  return made;
}

/** The origin of an address like `https://x.onrender.com/`, or "" when it is not one. */
export function originOf(address: unknown): string {
  if (typeof address !== "string" || address.trim() === "") return "";
  try {
    return new URL(address.trim()).origin;
  } catch {
    return "";
  }
}
