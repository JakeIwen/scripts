import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { fetchDiskStatus, fetchStoragePolicy } from './api';
import type { DiskStatus, StoragePolicy } from './types';

export const STORAGE_TILE_POLL_INTERVAL_MS = 30_000;
export const STORAGE_SHEET_POLL_INTERVAL_MS = 2_500;

export interface StorageResources {
  policy: PollingState<StoragePolicy>;
  disks: PollingState<DiskStatus>;
}

export function useStorageResources(sheetOpen: boolean): StorageResources {
  const intervalMs = sheetOpen ? STORAGE_SHEET_POLL_INTERVAL_MS : STORAGE_TILE_POLL_INTERVAL_MS;
  return {
    policy: usePollingResource({ load: fetchStoragePolicy, intervalMs }),
    disks: usePollingResource({ load: fetchDiskStatus, intervalMs }),
  };
}
