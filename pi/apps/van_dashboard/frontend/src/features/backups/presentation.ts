import type { StatusTone } from '../../components/StatusPill';
import { formatBytes, formatRelativeTime } from '../../utils/format';
import type { BackupEvidence, BackupProgress, BackupStatus, TimeMachineStatus } from './types';

export function backupTone(status: BackupStatus | null, error: Error | null): StatusTone {
  if (!status) return error ? 'bad' : 'neutral';
  if (error || status.operation.status === 'error' || status.stop.status === 'error') {
    return 'warning';
  }
  if (status.health === 'running') return 'warning';
  return status.health === 'attention' ? 'warning' : 'good';
}

export function lastSuccessLabel(timestamp: number | null): string {
  return timestamp === null ? 'Never' : formatRelativeTime(timestamp);
}

export function evidenceLabel(evidence: BackupEvidence): string {
  if (evidence.running) return evidence.progress?.detail ?? 'Running';
  if (evidence.stale) return `Stale · last success ${lastSuccessLabel(evidence.lastSuccessAt)}`;
  return `Last success ${lastSuccessLabel(evidence.lastSuccessAt)}`;
}

export function progressLabel(progress: BackupProgress | null): string | null {
  if (!progress) return null;
  const details = [
    progress.detail,
    progress.progressPercent === null ? null : `${progress.progressPercent}%`,
    progress.bytesProcessed === null ? null : formatBytes(progress.bytesProcessed),
    progress.filesRemaining === null ? null : `${progress.filesRemaining} files remaining`,
  ];
  return details.filter(Boolean).join(' · ');
}

export function timeMachineLabel(timeMachine: TimeMachineStatus): string {
  if (timeMachine.running) {
    const progress =
      timeMachine.progressPercent === null ? '' : ` · ${timeMachine.progressPercent}%`;
    return `Running${progress}`;
  }
  if (timeMachine.error) return timeMachine.error;
  return timeMachine.lastBackupAt === null
    ? 'No completed snapshot found'
    : `Last backup ${lastSuccessLabel(timeMachine.lastBackupAt)}`;
}

export function backupOperationLabel(status: BackupStatus): string {
  if (status.stop.status === 'running') return `Stopping ${status.stop.kind ?? 'backup'}…`;
  if (status.stop.status === 'error') return status.stop.error ?? 'Backup stop failed';
  const operation = status.operation;
  if (operation.status === 'idle') return `Updated ${formatRelativeTime(status.checkedAt)}`;
  if (operation.status === 'running') {
    return `${operation.kind ?? 'Backup'}${operation.target ? ` to ${operation.target}` : ''} running`;
  }
  if (operation.status === 'error') return operation.error ?? 'Backup operation failed';
  if (operation.status === 'stopped') return `${operation.kind ?? 'Backup'} stopped`;
  return `${operation.kind ?? 'Backup'} complete · ${formatRelativeTime(operation.completedAt)}`;
}
