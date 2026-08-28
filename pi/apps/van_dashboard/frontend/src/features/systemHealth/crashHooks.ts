import { useCallback, useEffect, useRef, useState } from 'react';

import type { PollingState } from '../../hooks/usePollingResource';
import { fetchCrashHistory } from './api';
import type { CrashHistory } from './types';

/** Load saved crash history on sheet open and thereafter only on demand. */
export function useCrashHistory(open: boolean): PollingState<CrashHistory> {
  const [data, setData] = useState<CrashHistory | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const requestRef = useRef<Promise<CrashHistory | null> | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(false);

  const refresh = useCallback((): Promise<CrashHistory | null> => {
    if (requestRef.current && !controllerRef.current?.signal.aborted) return requestRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    setRefreshing(true);
    const request = fetchCrashHistory(controller.signal)
      .then((history) => {
        if (!mountedRef.current) return null;
        setData(history);
        setError(null);
        setLastUpdatedAt(Date.now());
        return history;
      })
      .catch((reason: unknown) => {
        if (!mountedRef.current || controller.signal.aborted) return null;
        setError(reason instanceof Error ? reason : new Error(String(reason)));
        return null;
      })
      .finally(() => {
        if (requestRef.current === request) requestRef.current = null;
        if (controllerRef.current === controller) controllerRef.current = null;
        if (mountedRef.current) setRefreshing(false);
      });
    requestRef.current = request;
    return request;
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    if (open) void refresh();
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
      controllerRef.current = null;
      requestRef.current = null;
    };
  }, [open, refresh]);

  return {
    data,
    error,
    initialLoading: data === null && error === null,
    refreshing,
    lastUpdatedAt,
    refresh,
  };
}
