import { useEffect, useState } from 'react';

import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { fetchBackupStatus } from './api';
import { fetchOffsiteStatus, type OffsiteStatus } from './offsite';
import type { BackupStatus } from './types';

export const BACKUP_TILE_POLL_INTERVAL_MS = 30_000;
export const BACKUP_RUNNING_POLL_INTERVAL_MS = 2_500;
export const BACKUP_IDLE_SHEET_POLL_INTERVAL_MS = 10_000;

export function useBackupStatus(sheetOpen: boolean): PollingState<BackupStatus> {
  const [intervalMs, setIntervalMs] = useState(
    sheetOpen ? BACKUP_IDLE_SHEET_POLL_INTERVAL_MS : BACKUP_TILE_POLL_INTERVAL_MS,
  );
  const resource = usePollingResource({ load: fetchBackupStatus, intervalMs });

  useEffect(() => {
    const nextInterval = !sheetOpen
      ? BACKUP_TILE_POLL_INTERVAL_MS
      : resource.data?.health === 'running'
        ? BACKUP_RUNNING_POLL_INTERVAL_MS
        : BACKUP_IDLE_SHEET_POLL_INTERVAL_MS;
    setIntervalMs((current) => (current === nextInterval ? current : nextInterval));
  }, [resource.data?.health, sheetOpen]);

  return resource;
}

export const OFFSITE_RUNNING_POLL_INTERVAL_MS = 10_000;
export const OFFSITE_IDLE_POLL_INTERVAL_MS = 30_000;

/** Polls only while the Backups sheet is open, so the collapsed section's chip stays current. */
export function useOffsiteStatus(sheetOpen: boolean): PollingState<OffsiteStatus> {
  const [running, setRunning] = useState(false);
  const resource = usePollingResource({
    load: fetchOffsiteStatus,
    intervalMs: running ? OFFSITE_RUNNING_POLL_INTERVAL_MS : OFFSITE_IDLE_POLL_INTERVAL_MS,
    enabled: sheetOpen,
  });
  const isRunning = resource.data?.run.result === 'running';
  useEffect(() => setRunning(isRunning), [isRunning]);
  return resource;
}
