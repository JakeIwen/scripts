import { useCallback } from 'react';

import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { fetchComputeReport } from './api';
import type { ComputeRange, ComputeReport } from './types';

export const COMPUTE_TILE_INTERVAL_MS = 30_000;
export const COMPUTE_SHEET_INTERVAL_MS = 10_000;

export function useComputeReport(
  rangeHours: ComputeRange,
  sheetOpen: boolean,
): PollingState<ComputeReport> {
  const load = useCallback(
    (signal: AbortSignal) => fetchComputeReport(rangeHours, signal),
    [rangeHours],
  );

  return usePollingResource({
    load,
    intervalMs: sheetOpen ? COMPUTE_SHEET_INTERVAL_MS : COMPUTE_TILE_INTERVAL_MS,
  });
}
