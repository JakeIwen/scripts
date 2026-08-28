import { useCallback } from 'react';

import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { fetchSystemHealthReport } from './api';
import type { SystemHealthRange, SystemHealthReport } from './types';

export const SYSTEM_HEALTH_TILE_INTERVAL_MS = 30_000;
export const SYSTEM_HEALTH_SHEET_INTERVAL_MS = 10_000;

export function useSystemHealthReport(
  rangeHours: SystemHealthRange,
  sheetOpen: boolean,
): PollingState<SystemHealthReport> {
  const load = useCallback(
    (signal: AbortSignal) => fetchSystemHealthReport(rangeHours, signal),
    [rangeHours],
  );

  return usePollingResource({
    load,
    intervalMs: sheetOpen ? SYSTEM_HEALTH_SHEET_INTERVAL_MS : SYSTEM_HEALTH_TILE_INTERVAL_MS,
  });
}
