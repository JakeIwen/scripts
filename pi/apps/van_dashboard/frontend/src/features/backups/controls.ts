import { useCallback, useRef, useState } from 'react';

import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { setCloneCardNominalGb, startBackup, startBackupClone, stopBackup } from './api';
import type {
  BackupOperation,
  BackupStatus,
  BackupStopKind,
  BackupStopOperation,
  CloneCardNominalGb,
  HotspareStatus,
} from './types';

export type BackupControlNotice = (message: string, tone: 'normal' | 'error') => void;
export type BackupConfirmation = (message: string) => boolean;

interface PendingOperation {
  kind: string;
  startedAt: number;
}

export interface BackupControls {
  running: boolean;
  blocked: boolean;
  uncertainOutcome: boolean;
  pendingOperation: PendingOperation | null;
  pendingStop: PendingOperation | null;
  lastMessage: string | null;
  lastError: string | null;
  start: (kind: BackupStopKind) => Promise<void>;
  stop: (kind: BackupStopKind, status: BackupStatus) => Promise<void>;
  clone: (target: HotspareStatus) => Promise<void>;
  setCloneCardNominalGb: (value: CloneCardNominalGb) => Promise<void>;
  reconcile: (status: BackupStatus | null) => void;
}

function messageFrom(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function acceptedOperation(operation: BackupOperation): PendingOperation | null {
  return operation.status === 'running' && operation.kind && operation.startedAt !== null
    ? { kind: operation.kind, startedAt: operation.startedAt }
    : null;
}

function acceptedStop(operation: BackupStopOperation): PendingOperation | null {
  return operation.status === 'running' && operation.kind && operation.startedAt !== null
    ? { kind: operation.kind, startedAt: operation.startedAt }
    : null;
}

export function useBackupControls(
  refreshStatus: () => Promise<BackupStatus | null>,
  notify?: BackupControlNotice,
  confirm: BackupConfirmation = (message) => window.confirm(message),
): BackupControls {
  const { running, run } = useSingleFlightAction();
  const [uncertainOutcome, setUncertainOutcome] = useState(false);
  const [pending, setPending] = useState<PendingOperation | null>(null);
  const [pendingStop, setPendingStop] = useState<PendingOperation | null>(null);
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const lastStopKey = useRef<string | null>(null);

  const reconcile = useCallback(
    (status: BackupStatus | null) => {
      if (!status) return;
      setPending((current) => {
        if (!current) return null;
        return status.operation.startedAt === current.startedAt &&
          status.operation.kind === current.kind
          ? null
          : current;
      });
      setPendingStop((current) => {
        if (!current) return null;
        return status.stop.startedAt === current.startedAt && status.stop.kind === current.kind
          ? null
          : current;
      });
      const stopKey = `${status.stop.status}:${status.stop.kind ?? ''}:${status.stop.startedAt ?? ''}:${status.stop.completedAt ?? ''}`;
      if (
        lastStopKey.current !== null &&
        stopKey !== lastStopKey.current &&
        (status.stop.status === 'complete' || status.stop.status === 'error')
      ) {
        const label = status.stop.kind === 'exfat' ? 'EXFAT512' : 'Vanpi Borg';
        const message =
          status.stop.status === 'complete'
            ? `${label} backup stopped`
            : (status.stop.error ?? `Could not stop ${status.stop.kind ?? 'backup'}`);
        setLastError(status.stop.status === 'error' ? message : null);
        setLastMessage(status.stop.status === 'complete' ? message : null);
        notify?.(message, status.stop.status === 'error' ? 'error' : 'normal');
      }
      lastStopKey.current = stopKey;
    },
    [notify],
  );

  const perform = useCallback(
    async (request: () => Promise<{ message: string; backups: BackupStatus }>): Promise<void> => {
      await run(async () => {
        setLastError(null);
        let accepted: PendingOperation | null = null;
        let acceptedStopRequest: PendingOperation | null = null;
        let requestFailed = false;
        try {
          const result = await request();
          accepted = acceptedOperation(result.backups.operation);
          acceptedStopRequest = acceptedStop(result.backups.stop);
          setPending(accepted);
          setPendingStop(acceptedStopRequest);
          setLastMessage(result.message);
          notify?.(result.message, 'normal');
        } catch (reason) {
          requestFailed = true;
          const message = messageFrom(reason);
          setLastError(message);
          notify?.(message, 'error');
        } finally {
          const refreshed = await refreshStatus();
          if (refreshed) {
            setUncertainOutcome(false);
            if (
              accepted &&
              refreshed.operation.startedAt === accepted.startedAt &&
              refreshed.operation.kind === accepted.kind
            ) {
              setPending(null);
            }
            if (
              acceptedStopRequest &&
              refreshed.stop.startedAt === acceptedStopRequest.startedAt &&
              refreshed.stop.kind === acceptedStopRequest.kind
            ) {
              setPendingStop(null);
            }
          } else if (requestFailed || (!accepted && !acceptedStopRequest)) {
            setUncertainOutcome(true);
          }
        }
      });
    },
    [notify, refreshStatus, run],
  );

  const start = useCallback(
    async (kind: BackupStopKind): Promise<void> => {
      const accepted = confirm(
        kind === 'borg'
          ? 'Run the vanpi Borg backup now? This performs local snapshots, media sync, ' +
              'Borg create and retention, and any due hotspare clones. It may take hours.'
          : 'Create an EXFAT512 safety snapshot now? This mounts hdd1tb, creates a ' +
              'hard-link snapshot, applies retention, then unmounts and spins down the drive.',
      );
      if (accepted) await perform(() => startBackup(kind));
    },
    [confirm, perform],
  );

  const stop = useCallback(
    async (kind: BackupStopKind, status: BackupStatus): Promise<void> => {
      const cloneWarning =
        kind === 'borg' && status.borg.progress?.phase === 'cloning'
          ? ' The hotspare being written may be incomplete and will not be marked current.'
          : '';
      const accepted = confirm(
        kind === 'borg'
          ? `Stop the current vanpi Borg backup gracefully? Unfinished Borg work is discarded and the current run will not be marked successful.${cloneWarning}\n\nA scheduled run may retry later.`
          : 'Stop the current EXFAT512 snapshot gracefully? Its partial snapshot is retained ' +
              'for retry, then hdd1tb is unmounted and spun down.\n\nA scheduled run may retry later.',
      );
      if (accepted) await perform(() => stopBackup(kind));
    },
    [confirm, perform],
  );

  const clone = useCallback(
    async (target: HotspareStatus): Promise<void> => {
      const accepted = confirm(
        `Clone the current vanpi system to ${target.label}? Existing files on that hotspare ` +
          'will be synchronized or overwritten. Keep the card attached until completion.',
      );
      if (accepted) await perform(() => startBackupClone(target.label));
    },
    [confirm, perform],
  );

  const setCardCapacity = useCallback(
    async (value: CloneCardNominalGb): Promise<void> => {
      await perform(() => setCloneCardNominalGb(value));
    },
    [perform],
  );

  return {
    running,
    blocked: running || uncertainOutcome || pending !== null || pendingStop !== null,
    uncertainOutcome,
    pendingOperation: pending,
    pendingStop,
    lastMessage,
    lastError,
    start,
    stop,
    clone,
    setCloneCardNominalGb: setCardCapacity,
    reconcile,
  };
}
