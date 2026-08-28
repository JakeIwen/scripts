import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { fetchIgnitionMonitorStatus } from './api';
import type { IgnitionMonitorStatus } from './types';

export const IGNITION_TILE_POLL_INTERVAL_MS = 15_000;
export const IGNITION_SHEET_POLL_INTERVAL_MS = 5_000;

/** Poll one shared snapshot, faster only while the ignition sheet is visible. */
export function useIgnitionMonitorStatus(
  sheetOpen: boolean,
  enabled = true,
): PollingState<IgnitionMonitorStatus> {
  return usePollingResource({
    load: fetchIgnitionMonitorStatus,
    intervalMs: sheetOpen ? IGNITION_SHEET_POLL_INTERVAL_MS : IGNITION_TILE_POLL_INTERVAL_MS,
    enabled,
  });
}
