import { useCallback, useEffect, useState } from 'react';

import { useToast } from '../../components/ToastProvider';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { fetchVonstarStatus, performVonstarAction, requestVonstarAccessState } from './api';
import type {
  VonstarAccessState,
  VonstarActionName,
  VonstarAttempt,
  VonstarController,
  VonstarStatus,
} from './types';
import { VONSTAR_ACTIONS } from './types';

function errorValue(reason: unknown): Error {
  return reason instanceof Error ? reason : new Error(String(reason));
}

/**
 * vOnStar is intentionally point-in-time: one initial GET and explicit GET
 * reconciliation only. It has no timer, visibility refresh, mutation retry,
 * or automatic access-state request.
 */
export function useVonstarController(): VonstarController {
  const [status, setStatus] = useState<VonstarStatus | null>(null);
  const [accessState, setAccessState] = useState<VonstarAccessState | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastAttempt, setLastAttempt] = useState<VonstarAttempt | null>(null);
  const action = useSingleFlightAction();
  const { showToast } = useToast();

  const adoptStatus = useCallback((next: VonstarStatus) => {
    setStatus(next);
    setError(null);
    const result = next.lastResult;
    if (result?.kind === 'access_state' && result.ok && result.accessState) {
      setAccessState((current) => current ?? result.accessState ?? null);
    }
  }, []);

  const loadStatus = useCallback(
    async (signal: AbortSignal): Promise<VonstarStatus | null> => {
      setRefreshing(true);
      try {
        const next = await fetchVonstarStatus(signal);
        adoptStatus(next);
        return next;
      } catch (reason) {
        if (!signal.aborted) setError(errorValue(reason));
        return null;
      } finally {
        if (!signal.aborted) setRefreshing(false);
      }
    },
    [adoptStatus],
  );

  const refresh = useCallback(() => loadStatus(new AbortController().signal), [loadStatus]);

  useEffect(() => {
    const controller = new AbortController();
    void loadStatus(controller.signal).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [loadStatus]);

  const perform = useCallback(
    async (actionName: VonstarActionName): Promise<boolean> => {
      const completed = await action.run(async () => {
        const label = VONSTAR_ACTIONS[actionName].label;
        setLastAttempt({ label, state: 'working' });
        try {
          const result = await performVonstarAction(actionName);
          setLastAttempt({ label, state: result.result.ok ? 'ok' : 'failed' });
          if (!result.result.ok) {
            throw new Error(result.result.error ?? `${label} failed`);
          }
          showToast(result.message);
          return true;
        } catch (reason) {
          const failure = errorValue(reason);
          setLastAttempt({ label, state: 'failed', error: failure.message });
          showToast(failure.message, 'error');
          return false;
        } finally {
          await refresh();
        }
      });
      return completed === true;
    },
    [action, refresh, showToast],
  );

  const checkAccessState = useCallback(async (): Promise<boolean> => {
    const completed = await action.run(async () => {
      const label = 'Check Status';
      setLastAttempt({ label, state: 'working' });
      try {
        const result = await requestVonstarAccessState();
        if (!result.accessState) throw new Error('vOnStar returned no access-state snapshot');
        setAccessState(result.accessState);
        setLastAttempt({ label, state: 'ok' });
        showToast('Vehicle access state checked');
        return true;
      } catch (reason) {
        const failure = errorValue(reason);
        setLastAttempt({ label, state: 'failed', error: failure.message });
        showToast(failure.message, 'error');
        return false;
      } finally {
        await refresh();
      }
    });
    return completed === true;
  }, [action, refresh, showToast]);

  return {
    status,
    accessState,
    error,
    loading,
    refreshing,
    busy: action.running,
    lastAttempt,
    refresh,
    perform,
    checkAccessState,
  };
}
