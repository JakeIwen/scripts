import { useCallback, useState } from 'react';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import type { PollingState } from '../../hooks/usePollingResource';
import { useToast } from '../../components/ToastProvider';
import { JobStage, type WatchPending, type WatchStatus } from './schema';
import * as api from './api';
import { useAcceptedJob } from '../../components/media/useAcceptedJob';
import { watchCopy as copy } from './content';

export function useWatchActions(resource: PollingState<WatchStatus>) {
  const { running, run } = useSingleFlightAction();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<WatchPending | null>(null);
  const onUnknown = useCallback(() => setError(copy.unknownOutcome), []);
  const { pendingJob, acceptJob } = useAcceptedJob(resource, watchActive, onUnknown);
  const { showToast } = useToast();
  const status = resource.data;
  const checking =
    pending?.action === JobStage.Check ||
    !!status?.checking ||
    !!(status?.job && !status.job.finished_at) ||
    !!pendingJob;
  const blocked = running || checking || !status || !!resource.error;
  async function perform(
    operation: WatchPending,
    action: () => Promise<unknown>,
    message?: string,
  ) {
    if (blocked) return false;
    return (
      (await run(async () => {
        setError(null);
        setPending(operation);
        try {
          await action();
          if (message) showToast(message);
          await resource.refresh({ fresh: true });
          return true;
        } catch (reason) {
          const detail = reason instanceof Error ? reason.message : String(reason);
          const message = `Watchlist operation could not be confirmed. ${detail} Refresh status and check the result before retrying.`;
          setError(message);
          showToast(message, 'error');
          return false;
        } finally {
          setPending(null);
        }
      })) ?? false
    );
  }
  return {
    error,
    blocked,
    checking,
    pending,
    perform,
    clearError: () => setError(null),
    check: () =>
      perform({ action: JobStage.Check }, async () => {
        acceptJob(await api.check());
      }),
  };
}

function watchActive(status: WatchStatus) {
  return status.checking || !!(status.job && !status.job.finished_at);
}
