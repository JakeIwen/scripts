import type { PollingState } from '../../hooks/usePollingResource';
import { lastSuccessLabel } from './presentation';
import type { BackupStatus } from './types';
import './backups.css';

export interface BackupsTileProps {
  resource: PollingState<BackupStatus>;
  onOpen: () => void;
}

type BackupTileState = 'good' | 'running' | 'warning' | 'unknown';

function tileState(status: BackupStatus | null): BackupTileState {
  if (!status) return 'unknown';
  if (status.health === 'good') return 'good';
  if (status.health === 'running') return 'running';
  return 'warning';
}

function tilePill(status: BackupStatus | null): string {
  if (!status) return 'NO DATA';
  if (status.health === 'running') return 'RUNNING';
  return status.health === 'good' ? 'CURRENT' : 'CHECK';
}

function backupAge(timestamp: number | null): string | null {
  return timestamp === null ? null : lastSuccessLabel(timestamp);
}

function backupPresentation(status: BackupStatus) {
  const operationRunning = status.operation.status === 'running';
  const borgRunning = status.borg.running || (operationRunning && status.operation.kind === 'borg');
  const exfatRunning =
    status.exfatSnapshot.running || (operationRunning && status.operation.kind === 'exfat');
  const borgAge = backupAge(status.borg.lastSuccessAt);
  const exfatAge = backupAge(status.exfatSnapshot.lastSuccessAt);
  const openwrtAge = backupAge(status.openwrt.lastSuccessAt);
  const timeMachineAge = backupAge(status.timeMachine.lastBackupAt);
  const piSummary = `${borgAge ? `Borg ${borgAge}` : 'No Borg success'} · ${
    exfatAge ? `EXFAT ${exfatAge}` : 'No EXFAT snapshot'
  }`;
  const timeMachineSummary = timeMachineAge
    ? `TM ${timeMachineAge}`
    : (status.timeMachine.error ?? 'No Time Machine history');

  let summary = `${piSummary} · ${timeMachineSummary}`;
  if (borgRunning) {
    summary = status.borg.progress?.detail
      ? `Vanpi backup: ${status.borg.progress.detail}`
      : 'Creating a new vanpi Borg backup…';
  } else if (exfatRunning) {
    summary = status.exfatSnapshot.progress?.detail
      ? `EXFAT512 snapshot: ${status.exfatSnapshot.progress.detail}`
      : 'Creating an EXFAT512 safety snapshot…';
  } else if (operationRunning) {
    summary = `Cloning vanpi to ${status.operation.target ?? 'hotspare'}…`;
  } else if (status.timeMachine.running) {
    summary = 'Time Machine backup in progress';
  }

  return {
    summary,
    borg: borgAge ?? 'No successful archive',
    exfat: exfatAge ? `Snapshot ${exfatAge}` : 'No snapshot',
    openwrt: openwrtAge ? `Snapshot ${openwrtAge}` : 'No verified snapshot',
    timeMachine: status.timeMachine.running
      ? `Backing up${
          status.timeMachine.progressPercent === null
            ? ''
            : ` · ${status.timeMachine.progressPercent}%`
        }`
      : timeMachineSummary,
  };
}

export function BackupsTile({ resource, onOpen }: BackupsTileProps) {
  const status = resource.data;
  const state = tileState(status);
  const presentation = status ? backupPresentation(status) : null;

  return (
    <button
      type="button"
      className={`tile backups-tile backups-tile--${state}`}
      onClick={onOpen}
      aria-label="Open backup details"
      aria-haspopup="dialog"
    >
      <span className="backups-tile__pill">{tilePill(status)}</span>
      <span className="backups-tile__heading">
        <span className="backups-tile__icon" aria-hidden="true">
          🛟
        </span>
        <span className="backups-tile__title" role="heading" aria-level={2}>
          Backups
        </span>
      </span>
      <span className="backups-tile__summary">
        {presentation
          ? presentation.summary
          : resource.error
            ? 'Backup status unavailable'
            : 'Reading backup history…'}
      </span>
      <span className="backups-tile__status-lines">
        <span className="backups-tile__status-line">
          <span>Borg</span>
          <span>{presentation?.borg ?? 'Checking…'}</span>
        </span>
        <span className="backups-tile__status-line">
          <span>EXFAT512</span>
          <span>{presentation?.exfat ?? 'Checking…'}</span>
        </span>
        <span className="backups-tile__status-line">
          <span>OpenWrt</span>
          <span>{presentation?.openwrt ?? 'Checking…'}</span>
        </span>
        <span className="backups-tile__status-line">
          <span>m4mac</span>
          <span>{presentation?.timeMachine ?? 'Checking…'}</span>
        </span>
      </span>
    </button>
  );
}
