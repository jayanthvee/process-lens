/**
 * PL-105: the small slice of Chrome a side panel needs, typed here.
 *
 * The web app is built twice: for the website, and into the extension's panel. That second build
 * must not depend on @types/chrome, which belongs to the extension's own toolchain, so the panel
 * declares exactly the surface it calls. Nothing in the website build reaches these APIs: the
 * object is simply absent and every call is guarded.
 */

export interface ExtensionMessageListener {
  (message: unknown): void;
}

export interface ExtensionRuntime {
  sendMessage(message: unknown): Promise<unknown>;
  onMessage?: {
    addListener(listener: ExtensionMessageListener): void;
    removeListener?(listener: ExtensionMessageListener): void;
  };
}

export interface ExtensionLocalStorage {
  get(keys: string[]): Promise<Record<string, unknown>>;
  set(items: Record<string, unknown>): Promise<void>;
}

export interface ExtensionChrome {
  runtime?: ExtensionRuntime;
  storage?: { local?: ExtensionLocalStorage };
  tabs?: {
    query(query: { active: boolean; currentWindow: boolean }): Promise<{ id?: number }[]>;
  };
}

/** This browser's extension, or `undefined` when the page is the website or a test. */
export function extensionChrome(): ExtensionChrome | undefined {
  return (globalThis as { chrome?: ExtensionChrome }).chrome;
}
