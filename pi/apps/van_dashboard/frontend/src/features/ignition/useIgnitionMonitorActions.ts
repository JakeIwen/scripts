import { useCallback } from 'react';

import { useToast } from '../../components/ToastProvider';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import {
  fetchIgnitionMonitorStatus,
  pauseIgnitionMonitoring,
  resumeIgnitionMonitoring,
} from './api';
import type {
  IgnitionMonitorActions,
  IgnitionMonitorMutationResult,
  IgnitionMonitorStatus,
} from './types';

interface IgnitionActionsOptions {
  onAuthoritativeStatus: (status: IgnitionMonitorStatus) => void;
}

function errorValue(reason: unknown): Error {
  return reason instanceof Error ? reason : new Error(String(reason));
}

/** Serialize control requests; failed mutations are never retried. */
export function useIgnitionMonitorActions({
  onAuthoritativeStatus,
}: IgnitionActionsOptions): IgnitionMonitorActions {
  const { running, run } = useSingleFlightAction();
  const { showToast } = useToast();

  const reconcileFailure = useCallback(async () => {
    try {
      const status = await fetchIgnitionMonitorStatus(new AbortController().signal);
      onAuthoritativeStatus(status);
    } catch {
      // The normal bounded poller resumes after the action. Keep the original
      // mutation failure as the user-facing error.
    }
  }, [onAuthoritativeStatus]);

  const execute = useCallback(
    async (mutation: () => Promise<IgnitionMonitorMutationResult>): Promise<boolean> => {
      const completed = await run(async () => {
        try {
          const result = await mutation();
          onAuthoritativeStatus(result.status);
          showToast(result.message);
          return true;
        } catch (reason) {
          const error = errorValue(reason);
          await reconcileFailure();
          showToast(error.message, 'error');
          throw error;
        }
      });
      return completed === true;
    },
    [onAuthoritativeStatus, reconcileFailure, run, showToast],
  );

  const pause = useCallback(
    (minutes: number) => execute(() => pauseIgnitionMonitoring(minutes)),
    [execute],
  );
  const resume = useCallback(() => execute(resumeIgnitionMonitoring), [execute]);

  return { running, pause, resume };
}
