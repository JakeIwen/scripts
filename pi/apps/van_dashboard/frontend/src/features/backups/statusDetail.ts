import { iCloudTileTone } from './cloudPresentation';
import { lastSuccessLabel } from './presentation';
import type { ICloudStatus } from './icloud';
import type { BackupStatus } from './types';

function cloudIssue(label: string, cloud: ICloudStatus | null): string | null {
  if (!cloud) return null;
  if (!cloud.available) return `${label}: backup status unavailable.`;
  if (!cloud.attention && !iCloudTileTone(cloud)) return null;
  return `${label}: ${cloud.message} Last verified ${lastSuccessLabel(cloud.lastSuccessAt)}; scheduled every ${cloud.intervalDays} days.`;
}

export function backupStatusDetail(status: BackupStatus | null, error: Error | null): string {
  if (!status) return error?.message ?? 'Waiting for backup status.';
  const issues: string[] = [];
  if (error) issues.push(`Latest refresh failed: ${error.message}`);
  for (const [label, evidence] of [
    ['Borg', status.borg],
    ['EXFAT512', status.exfatSnapshot],
    ['OpenWrt', status.openwrt],
  ] as const) {
    if (evidence.stale) {
      issues.push(
        `${label}: ${evidence.lastSuccessAt === null ? 'no successful backup recorded' : `last success ${lastSuccessLabel(evidence.lastSuccessAt)}`}; freshness limit ${evidence.staleHours} hours.`,
      );
    }
  }
  for (const card of status.hotswaps) {
    if (card.stale)
      issues.push(
        `${card.label}: clone overdue; last clone ${lastSuccessLabel(card.lastCloneAt)}${card.attached ? '' : '; card not attached'}.`,
      );
  }
  if (!status.timeMachine.available || status.timeMachine.lastBackupAt === null) {
    issues.push(`Time Machine: ${status.timeMachine.error ?? 'no completed backup found'}.`);
  }
  for (const issue of [
    cloudIssue('Mac · iCloud', status.timeMachineIcloud),
    cloudIssue('Pi · iCloud', status.icloud),
  ]) {
    if (issue) issues.push(issue);
  }
  if (status.operation.error) issues.push(`Backup operation: ${status.operation.error}`);
  if (status.stop.error) issues.push(`Stopping backup: ${status.stop.error}`);
  if (status.health === 'running') issues.unshift('A backup or cleanup operation is running.');
  return (
    issues.join('\n') ||
    (status.health === 'good'
      ? 'All monitored backups and spare-card clones are within their freshness limits.'
      : 'The backup service reports an issue; open backup details for the latest evidence.')
  );
}
