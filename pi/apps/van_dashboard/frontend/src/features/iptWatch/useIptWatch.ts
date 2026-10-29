import { useRef, useState } from 'react';
import { usePollingResource } from '../../hooks/usePollingResource';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { useToast } from '../../components/ToastProvider';
import * as api from './api';
import {
  CandidateAction,
  type Candidate,
  type CandidateOperation,
  type WantRequest,
} from './schema';

export function useIptWatch(open: boolean) {
  const resource = usePollingResource({ load: api.fetchStatus, intervalMs: open ? 2000 : 15000 });
  const { running, run } = useSingleFlightAction();
  const [error, setError] = useState<string | null>(null);
  const [startingCheck, setStartingCheck] = useState(false);
  const confirmedPacks = useRef(new Set<string>());
  const { showToast } = useToast();
  const status = resource.data;
  const checking =
    startingCheck || !!status?.checking || !!(status?.job && !status.job.finished_at);
  const blocked = running || checking || !status || !!resource.error;

  async function perform(action: () => Promise<unknown>, message?: string) {
    if (blocked) return false;
    return (
      (await run(async () => {
        setError(null);
        try {
          await action();
          if (message) showToast(message);
          await resource.refresh();
          return true;
        } catch (reason) {
          const message = reason instanceof Error ? reason.message : String(reason);
          setError(message);
          showToast(message, 'error');
          return false;
        }
      })) ?? false
    );
  }
  async function changeCandidate(action: CandidateOperation, candidate: Candidate) {
    if (blocked) return false;
    if (
      action === CandidateAction.Download &&
      candidate.episode === null &&
      !confirmedPacks.current.has(candidate.torrent_id)
    ) {
      if (
        !window.confirm(
          `Download the season pack “${candidate.name}”? This may include episodes already on disk.`,
        )
      )
        return false;
      confirmedPacks.current.add(candidate.torrent_id);
    }
    return perform(
      () => api.candidate(action, candidate.torrent_id),
      action === CandidateAction.Download
        ? 'Torrent saved to the watch folder'
        : 'Candidate dismissed',
    );
  }
  return {
    resource,
    error,
    blocked,
    checking,
    check: () =>
      perform(async () => {
        setStartingCheck(true);
        try {
          await api.check();
        } catch (error) {
          setStartingCheck(false);
          throw error;
        }
        // Keep admission blocked until the subsequent status read confirms the job.
        try {
          await resource.refresh();
        } finally {
          setStartingCheck(false);
        }
      }),
    wantlist: (payload: WantRequest) => perform(() => api.wantlist(payload), 'Watchlist updated'),
    candidate: changeCandidate,
  };
}
export type IptWatch = ReturnType<typeof useIptWatch>;
