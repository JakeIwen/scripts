import type { PollingState } from '../../hooks/usePollingResource';
import { KeyValueList } from '../../components/KeyValueList';
import { StatusPill } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import { backupTone, evidenceLabel, timeMachineLabel } from './presentation';
import type { BackupStatus } from './types';
import './backups.css';

export interface BackupsTileProps {
  resource: PollingState<BackupStatus>;
  onOpen: () => void;
}

export function BackupsTile({ resource, onOpen }: BackupsTileProps) {
  const status = resource.data;
  const tone = backupTone(status, resource.error);
  const items = status
    ? [
        { label: 'Vanpi Borg', value: evidenceLabel(status.borg) },
        { label: 'EXFAT snapshot', value: evidenceLabel(status.exfatSnapshot) },
        { label: 'OpenWrt', value: evidenceLabel(status.openwrt) },
        { label: 'Time Machine', value: timeMachineLabel(status.timeMachine) },
      ]
    : [
        { label: 'Vanpi Borg', value: '—' },
        { label: 'EXFAT snapshot', value: '—' },
        { label: 'OpenWrt', value: '—' },
        { label: 'Time Machine', value: '—' },
      ];
  const summary = status
    ? status.health === 'running'
      ? 'A backup operation is running'
      : status.health === 'attention'
        ? 'Backup evidence needs attention'
        : 'Backup evidence is current'
    : 'Reading backup evidence';

  return (
    <Tile
      icon="🛟"
      title="Backups"
      summary={resource.error && !status ? resource.error.message : summary}
      status={<StatusPill tone={tone}>{status?.health ?? 'Loading'}</StatusPill>}
      tone={tone}
      onClick={onOpen}
      ariaLabel="Open backup details"
      className="backups-tile"
    >
      <KeyValueList items={items} />
      <aside className="backups-read-only" aria-label="Backup controls available">
        <strong>Controls enabled</strong>
        <span>Open to start, stop, or clone guarded backup jobs.</span>
      </aside>
    </Tile>
  );
}
