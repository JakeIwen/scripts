import { useCallback, useState } from 'react';

import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { startDiskAction, updateStoragePolicy } from './api';
import type {
  DiskAction,
  DiskOperation,
  DiskStatus,
  ManagedDisk,
  StoragePolicy,
  StoragePolicyField,
} from './types';

export type StorageControlNotice = (message: string, tone: 'normal' | 'error') => void;
export type StorageConfirmation = (message: string) => boolean;

interface PendingDiskOperation {
  action: DiskAction;
  label: string;
  startedAt: number;
}

export interface StorageControls {
  running: boolean;
  blocked: boolean;
  uncertainOutcome: boolean;
  pendingDiskOperation: PendingDiskOperation | null;
  lastMessage: string | null;
  lastError: string | null;
  setPolicy: (field: StoragePolicyField, enabled: boolean) => Promise<void>;
  runDiskAction: (disk: ManagedDisk, action: DiskAction) => Promise<void>;
  reconcileDiskStatus: (status: DiskStatus | null) => void;
}

function messageFrom(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function diskConfirmation(disk: ManagedDisk, action: DiskAction): string | null {
  if (action === 'eject') {
    const result = disk.automaticMount
      ? 'Automatic mounting will resume in one minute.'
      : 'It will stay unmounted until requested here or by the backup tools.';
    return `Unmount ${disk.label}? Active disk users will be stopped safely. ${result}`;
  }
  if (action === 'repair') {
    return (
      `Repair ${disk.label}? This safely unmounts it if needed, runs automatic ` +
      `${disk.filesystem ?? 'filesystem'} repair, verifies it read-only, and restores its ` +
      'previous mount state only after verification succeeds. This can take a long time.'
    );
  }
  return null;
}

function pendingOperation(operation: DiskOperation): PendingDiskOperation | null {
  if (
    operation.status !== 'running' ||
    operation.action === null ||
    operation.label === null ||
    operation.startedAt === null
  ) {
    return null;
  }
  return {
    action: operation.action,
    label: operation.label,
    startedAt: operation.startedAt,
  };
}

export function useStorageControls(
  refreshPolicy: () => Promise<StoragePolicy | null>,
  refreshDisks: () => Promise<DiskStatus | null>,
  notify?: StorageControlNotice,
  confirm: StorageConfirmation = (message) => window.confirm(message),
): StorageControls {
  const { running, run } = useSingleFlightAction();
  const [uncertainOutcome, setUncertainOutcome] = useState(false);
  const [pendingDisk, setPendingDisk] = useState<PendingDiskOperation | null>(null);
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const reconcileDiskStatus = useCallback((status: DiskStatus | null) => {
    if (!status) return;
    setPendingDisk((pending) => {
      if (!pending) return null;
      const observed = status.operation;
      return observed.startedAt === pending.startedAt &&
        observed.label === pending.label &&
        observed.action === pending.action
        ? null
        : pending;
    });
  }, []);

  const setPolicy = useCallback(
    async (field: StoragePolicyField, enabled: boolean): Promise<void> => {
      await run(async () => {
        setLastError(null);
        let succeeded = false;
        try {
          const result = await updateStoragePolicy(field, enabled);
          succeeded = true;
          setLastMessage(result.message);
          notify?.(result.message, 'normal');
        } catch (reason) {
          const message = messageFrom(reason);
          setLastError(message);
          notify?.(message, 'error');
        } finally {
          const refreshed = await refreshPolicy();
          setUncertainOutcome(refreshed === null);
          if (succeeded && refreshed === null) {
            notify?.('Policy changed, but current policy could not be refreshed', 'error');
          }
        }
      });
    },
    [notify, refreshPolicy, run],
  );

  const runDiskAction = useCallback(
    async (disk: ManagedDisk, action: DiskAction): Promise<void> => {
      const confirmation = diskConfirmation(disk, action);
      if (confirmation && !confirm(confirmation)) return;

      await run(async () => {
        setLastError(null);
        let accepted: PendingDiskOperation | null = null;
        let requestFailed = false;
        try {
          const result = await startDiskAction(disk.label, action);
          accepted = pendingOperation(result.diskStatus.operation);
          setPendingDisk(accepted);
          setLastMessage(result.message);
          notify?.(result.message, 'normal');
        } catch (reason) {
          requestFailed = true;
          const message = messageFrom(reason);
          setLastError(message);
          notify?.(message, 'error');
        } finally {
          const refreshed = await refreshDisks();
          if (refreshed) {
            setUncertainOutcome(false);
            if (accepted) {
              const observed = refreshed.operation;
              if (
                observed.startedAt === accepted.startedAt &&
                observed.label === accepted.label &&
                observed.action === accepted.action
              ) {
                setPendingDisk(null);
              }
            }
          } else if (requestFailed || accepted === null) {
            setUncertainOutcome(true);
          }
        }
      });
    },
    [confirm, notify, refreshDisks, run],
  );

  return {
    running,
    blocked: running || uncertainOutcome || pendingDisk !== null,
    uncertainOutcome,
    pendingDiskOperation: pendingDisk,
    lastMessage,
    lastError,
    setPolicy,
    runDiskAction,
    reconcileDiskStatus,
  };
}
