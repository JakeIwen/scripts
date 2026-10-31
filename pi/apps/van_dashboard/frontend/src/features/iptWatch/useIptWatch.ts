import { usePollingResource } from '../../hooks/usePollingResource';
import * as api from './api';
import {
  CandidateAction,
  type Candidate,
  type CandidateOperation,
  type WantRequest,
} from './schema';
import { successLabels } from './content';
import { useWatchActions } from './useWatchActions';

export function useIptWatch(open: boolean) {
  const resource = usePollingResource({ load: api.fetchStatus, intervalMs: open ? 2000 : 15000 });
  const actions = useWatchActions(resource);
  async function changeCandidate(action: CandidateOperation, candidate: Candidate) {
    if (actions.blocked) return false;
    if (
      action === CandidateAction.Download &&
      candidate.episode === null &&
      !window.confirm(
        `Download the season pack “${candidate.name}”? This may include episodes already on disk.`,
      )
    )
      return false;
    return actions.perform(
      { action, id: candidate.torrent_id },
      () => api.candidate(action, candidate.torrent_id),
      successLabels[action],
    );
  }
  return {
    resource,
    error: actions.error,
    blocked: actions.blocked,
    checking: actions.checking,
    pending: actions.pending,
    clearError: actions.clearError,
    check: actions.check,
    wantlist: (payload: WantRequest) =>
      actions.perform(
        { action: payload.action, id: payload.name },
        () => api.wantlist(payload),
        successLabels[payload.action],
      ),
    candidate: changeCandidate,
  };
}
export type IptWatch = ReturnType<typeof useIptWatch>;
