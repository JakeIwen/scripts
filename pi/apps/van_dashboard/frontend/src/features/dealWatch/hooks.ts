import { useEffect, useRef } from 'react';

import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { fetchDealWatchStatus } from './api';
import type { DealWatchStatus } from './types';

export const DEAL_WATCH_POLL_INTERVAL_MS = 30_000;

/**
 * Keep one visible-page poller for the tile and sheet. Opening the sheet asks
 * for a fresh snapshot immediately without creating a second polling loop.
 */
export function useDealWatchStatus(sheetOpen: boolean): PollingState<DealWatchStatus> {
  const resource = usePollingResource({
    load: fetchDealWatchStatus,
    intervalMs: DEAL_WATCH_POLL_INTERVAL_MS,
  });
  const previouslyOpen = useRef(sheetOpen);

  useEffect(() => {
    const justOpened = sheetOpen && !previouslyOpen.current;
    previouslyOpen.current = sheetOpen;
    if (justOpened) void resource.refresh();
  }, [resource.refresh, sheetOpen]);

  return resource;
}
