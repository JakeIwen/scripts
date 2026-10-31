import { useCallback, useState } from 'react';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import type { PollingState } from '../../hooks/usePollingResource';
import { useToast } from '../../components/ToastProvider';
import { Stage, type OrganizerStatus } from './schema';
import { operationError, organizerCopy as copy } from './content';
import { useAcceptedJob } from '../../components/media/useAcceptedJob';
import * as api from './api';

export function useOrganizerActions(resource: PollingState<OrganizerStatus>) {
  const [error, setError] = useState<string | null>(null);
  const [actionLabel, setActionLabel] = useState<string | null>(null);
  const [undoRunId, setUndoRunId] = useState<string | null>(null);
  const onUnknown = useCallback(() => setError(copy.unknownOutcome), []);
  const { pendingJob, acceptJob } = useAcceptedJob(resource, organizerActive, onUnknown);
  const { running, run } = useSingleFlightAction();
  const { showToast } = useToast();
  const status = resource.data;
  const job = status?.job?.started_at === pendingJob?.started_at ? status?.job : pendingJob;
  const busyStage =
    status?.busy?.stage ??
    (status?.job && !status.job.finished_at ? status.job.stage : null) ??
    (job && !job.finished_at ? job.stage : null);
  const blocked = running || !!busyStage || !status || !!resource.error;
  async function perform(
    action: () => Promise<unknown>,
    label: string,
    message?: string,
  ): Promise<boolean> {
    if (blocked) return false;
    return (
      (await run(async () => {
        setError(null);
        setActionLabel(label);
        try {
          await action();
          if (message) showToast(message);
          await resource.refresh({ fresh: true });
          return true;
        } catch (reason) {
          const message = operationError(label, reason);
          setError(message);
          showToast(message, 'error');
          return false;
        }
      })) ?? false
    );
  }
  async function start(
    stage: typeof Stage.Propose | typeof Stage.Apply | typeof Stage.Undo,
    payload: { plan_id?: string; auto?: boolean; run_id?: string },
  ) {
    setUndoRunId(payload.run_id ?? null);
    acceptJob(await api.startJob(stage, payload));
  }
  return {
    error,
    setError,
    announce: showToast,
    blocked,
    busyStage,
    undoRunId: running || busyStage === Stage.Undo ? undoRunId : null,
    actionLabel: running || busyStage ? actionLabel : null,
    perform,
    start,
  };
}

function organizerActive(status: OrganizerStatus) {
  return !!status.busy || !!(status.job && !status.job.finished_at);
}
