import { useCallback, useEffect, useRef, useState } from 'react';

export interface PollingState<T> {
  data: T | null;
  error: Error | null;
  initialLoading: boolean;
  refreshing: boolean;
  lastUpdatedAt: number | null;
  refresh: () => Promise<T | null>;
}

interface PollingOptions<T> {
  load: (signal: AbortSignal) => Promise<T>;
  intervalMs: number;
  enabled?: boolean;
  pauseWhenHidden?: boolean;
}

/**
 * Poll one resource sequentially. A new request is scheduled only after the
 * previous request settles, so a slow vanpi response can never build a queue.
 */
export function usePollingResource<T>({
  load,
  intervalMs,
  enabled = true,
  pauseWhenHidden = true,
}: PollingOptions<T>): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const requestRef = useRef<Promise<T | null> | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(false);

  const refresh = useCallback((): Promise<T | null> => {
    if (requestRef.current && !controllerRef.current?.signal.aborted) {
      return requestRef.current;
    }
    requestRef.current = null;

    const controller = new AbortController();
    controllerRef.current = controller;
    setRefreshing(true);

    const request = load(controller.signal)
      .then((next) => {
        if (!mountedRef.current) return null;
        setData(next);
        setError(null);
        setLastUpdatedAt(Date.now());
        return next;
      })
      .catch((reason: unknown) => {
        if (!mountedRef.current || controller.signal.aborted) return null;
        setError(reason instanceof Error ? reason : new Error(String(reason)));
        return null;
      })
      .finally(() => {
        if (requestRef.current !== request) return;
        requestRef.current = null;
        if (controllerRef.current === controller) controllerRef.current = null;
        if (mountedRef.current) setRefreshing(false);
      });

    requestRef.current = request;
    return request;
  }, [load]);

  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) return () => void (mountedRef.current = false);

    let timeout: number | null = null;
    let stopped = false;

    const schedule = () => {
      if (stopped) return;
      if (timeout !== null) window.clearTimeout(timeout);
      timeout = window.setTimeout(runAndSchedule, intervalMs);
    };

    const runAndSchedule = async () => {
      if (stopped) return;
      if (!pauseWhenHidden || !document.hidden) await refresh();
      schedule();
    };

    const wake = () => {
      if (document.hidden && pauseWhenHidden) return;
      if (timeout !== null) window.clearTimeout(timeout);
      void refresh().finally(schedule);
    };

    void runAndSchedule();
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('focus', wake);
    window.addEventListener('pageshow', wake);

    return () => {
      stopped = true;
      mountedRef.current = false;
      if (timeout !== null) window.clearTimeout(timeout);
      controllerRef.current?.abort();
      controllerRef.current = null;
      requestRef.current = null;
      document.removeEventListener('visibilitychange', wake);
      window.removeEventListener('focus', wake);
      window.removeEventListener('pageshow', wake);
    };
  }, [enabled, intervalMs, pauseWhenHidden, refresh]);

  return {
    data,
    error,
    initialLoading: data === null && error === null,
    refreshing,
    lastUpdatedAt,
    refresh,
  };
}
