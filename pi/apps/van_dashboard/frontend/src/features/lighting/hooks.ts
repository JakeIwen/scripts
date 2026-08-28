import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { fetchLightingStatus } from './api';
import type { LightingStatus } from './types';

export const LIGHTING_TILE_POLL_INTERVAL_MS = 30_000;
export const LIGHTING_SHEET_POLL_INTERVAL_MS = 10_000;

/** Poll one shared lighting snapshot, faster only while its detail sheet is open. */
export function useLightingStatus(
  sheetOpen: boolean,
  enabled = true,
): PollingState<LightingStatus> {
  return usePollingResource({
    load: fetchLightingStatus,
    intervalMs: sheetOpen ? LIGHTING_SHEET_POLL_INTERVAL_MS : LIGHTING_TILE_POLL_INTERVAL_MS,
    enabled,
  });
}
