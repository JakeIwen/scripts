import { useEffect, useState } from 'react';
import type { PollingState } from '../../hooks/usePollingResource';

type JobStatus = { job: { started_at: string } | null };

/** Accepted jobs need reconciliation, never an automatic replay after a lost response. */
export function useAcceptedJob<S extends JobStatus>(
  resource: PollingState<S>,
  isActive: (status: S) => boolean,
  onUnknown: () => void,
) {
  const [pendingJob, acceptJob] = useState<S['job'] | null>(null);
  const reported = resource.data?.job;
  const active = resource.data ? isActive(resource.data) : false;
  const { error, refresh } = resource;
  useEffect(() => {
    if (!pendingJob) return;
    if (
      reported &&
      (reported.started_at === pendingJob.started_at ||
        Date.parse(reported.started_at) > Date.parse(pendingJob.started_at))
    ) {
      acceptJob(null);
      return;
    }
    if (reported !== null || active || error) return;
    let cancelled = false;
    // A poll begun before acceptance cannot prove that a job disappeared.
    void refresh({ fresh: true }).then((next) => {
      if (cancelled || !next || next.job || isActive(next)) return;
      acceptJob(null);
      onUnknown();
    });
    return () => {
      cancelled = true;
    };
  }, [pendingJob, reported, active, error, refresh, isActive, onUnknown]);
  return { pendingJob, acceptJob };
}
