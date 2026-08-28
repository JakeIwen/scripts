import { useCallback } from 'react';

import { useToast } from '../../components/ToastProvider';
import type { PollingState } from '../../hooks/usePollingResource';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import {
  addDealWatchListing,
  addDealWatchSearch,
  checkDealWatchListings,
  checkDealWatchSearch,
  dismissDealWatchSearchResult,
  editDealWatchListing,
  removeDealWatchListing,
  removeDealWatchSearch,
  saveDealWatchSchedule,
  setDealWatchListingMute,
} from './api';
import type {
  DealWatchListingDraft,
  DealWatchMutationResult,
  DealWatchSearchDraft,
  DealWatchStatus,
} from './types';

export interface DealWatchControls {
  busy: boolean;
  addListing: (draft: DealWatchListingDraft) => Promise<boolean>;
  editListing: (id: number, draft: DealWatchListingDraft) => Promise<boolean>;
  removeListing: (id: number) => Promise<boolean>;
  setListingMute: (id: number, days: number) => Promise<boolean>;
  checkListings: (target: number | 'all') => Promise<boolean>;
  addSearch: (draft: DealWatchSearchDraft) => Promise<boolean>;
  removeSearch: (id: number) => Promise<boolean>;
  dismissSearchResult: (searchId: number, itemId: string) => Promise<boolean>;
  checkSearch: (target: number | 'all') => Promise<boolean>;
  saveSchedule: (expression: string) => Promise<boolean>;
}

type Mutation = () => Promise<DealWatchMutationResult>;
type ShowToast = (message: string, tone?: 'normal' | 'error') => void;

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

export async function executeDealWatchMutation(
  mutation: Mutation,
  refresh: PollingState<DealWatchStatus>['refresh'],
  showToast: ShowToast,
): Promise<boolean> {
  try {
    const result = await mutation();
    showToast(result.message);
    return true;
  } catch (reason) {
    showToast(errorMessage(reason), 'error');
    return false;
  } finally {
    // Never guess whether a failed request changed durable state.
    await refresh();
  }
}

/** All Deal Watch writes share one flight, matching the backend's serial workflow. */
export function useDealWatchControls(resource: PollingState<DealWatchStatus>): DealWatchControls {
  const { showToast } = useToast();
  const { running, run } = useSingleFlightAction();

  const execute = useCallback(
    async (mutation: Mutation): Promise<boolean> => {
      const completed = await run(() =>
        executeDealWatchMutation(mutation, resource.refresh, showToast),
      );
      return completed ?? false;
    },
    [resource.refresh, run, showToast],
  );

  return {
    busy: running,
    addListing: (draft) => execute(() => addDealWatchListing(draft)),
    editListing: (id, draft) => execute(() => editDealWatchListing(id, draft)),
    removeListing: (id) => execute(() => removeDealWatchListing(id)),
    setListingMute: (id, days) => execute(() => setDealWatchListingMute(id, days)),
    checkListings: (target) => execute(() => checkDealWatchListings(target)),
    addSearch: (draft) => execute(() => addDealWatchSearch(draft)),
    removeSearch: (id) => execute(() => removeDealWatchSearch(id)),
    dismissSearchResult: (searchId, itemId) =>
      execute(() => dismissDealWatchSearchResult(searchId, itemId)),
    checkSearch: (target) => execute(() => checkDealWatchSearch(target)),
    saveSchedule: (expression) => execute(() => saveDealWatchSchedule(expression)),
  };
}
