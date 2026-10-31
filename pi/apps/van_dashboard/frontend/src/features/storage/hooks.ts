import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { fetchDiskStats, fetchDiskStatus, fetchStoragePolicy } from './api';
import { DISK_STATS_POLL_INTERVAL_MS } from './diskStats';
import type { DiskStats, DiskStatus, StoragePolicy } from './types';

export const STORAGE_TILE_POLL_INTERVAL_MS = 30_000;
export const STORAGE_SHEET_POLL_INTERVAL_MS = 2_500;

export interface StorageResources {
  policy: PollingState<StoragePolicy>;
  disks: PollingState<DiskStatus>;
  stats: PollingState<DiskStats>;
}

export function useStorageResources(sheetOpen: boolean): StorageResources {
  const intervalMs = sheetOpen ? STORAGE_SHEET_POLL_INTERVAL_MS : STORAGE_TILE_POLL_INTERVAL_MS;
  return {
    policy: usePollingResource({ load: fetchStoragePolicy, intervalMs }),
    disks: usePollingResource({ load: fetchDiskStatus, intervalMs }),
    // Only the open sheet shows I/O and temperature, and both change by the minute.
    stats: usePollingResource({
      load: fetchDiskStats,
      intervalMs: DISK_STATS_POLL_INTERVAL_MS,
      enabled: sheetOpen,
    }),
  };
}
