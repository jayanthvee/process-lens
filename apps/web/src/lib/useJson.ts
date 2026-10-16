/**
 * One hook for every read: fetch a path once, and poll it while a number of milliseconds is given.
 *
 * The interval stops the moment the component goes away or `pollMs` becomes `null`, so a page that
 * is finished polling makes no further requests.
 */

import { useCallback, useEffect, useState } from "react";

import { apiGet, errorText } from "./api";

/** How often a live page asks the API again. */
export const POLL_INTERVAL_MS = 2000;

export interface JsonState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  /** Fetch once more now, without waiting for the next poll. */
  reload: () => void;
}

export function useJson<T>(path: string | null, pollMs: number | null = null): JsonState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(path !== null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (path === null) {
      setLoading(false);
      return;
    }
    setLoading(true);
    const controller = new AbortController();
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const payload = await apiGet<T>(path, controller.signal);
        if (!cancelled) {
          setData(payload);
          setError(null);
        }
      } catch (failure) {
        if (!cancelled && !controller.signal.aborted) setError(errorText(failure));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    // A number means "keep asking"; `null` means "this page is done".
    const interval =
      pollMs === null ? undefined : setInterval(() => void load(), Math.max(pollMs, 1));
    return () => {
      cancelled = true;
      controller.abort();
      if (interval !== undefined) clearInterval(interval);
    };
  }, [path, pollMs, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  return { data, error, loading, reload };
}
