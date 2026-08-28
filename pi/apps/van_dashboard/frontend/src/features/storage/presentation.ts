import type { StatusTone } from '../../components/StatusPill';
import { formatRelativeTime } from '../../utils/format';
import type { DiskStatus, ManagedDisk, StoragePolicy } from './types';

export function storageTone(
  policy: StoragePolicy | null,
  disks: DiskStatus | null,
  error: Error | null,
): StatusTone {
  if (!policy && !disks) return error ? 'bad' : 'neutral';
  if (disks?.disks.some((disk) => disk.error || disk.health.state === 'critical')) {
    return 'bad';
  }
  if (
    error ||
    disks?.operation.status === 'running' ||
    disks?.operation.status === 'error' ||
    disks?.disks.some((disk) => disk.health.state === 'warning')
  ) {
    return 'warning';
  }
  return 'good';
}

export function storageSummary(policy: StoragePolicy | null): string {
  if (!policy) return 'Reading disk and torrent policy';
  const mounted = policy.runtime.disksMounted ? 'mounted' : 'unmounted';
  const torrents = policy.runtime.qbittorrentRunning ? 'running' : 'stopped';
  return `Disks ${mounted} · qBittorrent ${torrents}`;
}

export function requestedPolicyState(
  policy: StoragePolicy,
  field: keyof Pick<StoragePolicy, 'disksEnabled' | 'torrentsEnabled' | 'allowStarlinkTorrents'>,
): string {
  if (!policy[field]) return 'Off';
  if (field === 'torrentsEnabled' && !policy.disksEnabled) return 'On · blocked';
  if (field === 'allowStarlinkTorrents' && (!policy.disksEnabled || !policy.torrentsEnabled)) {
    return 'On · blocked';
  }
  return 'On';
}

export function diskStateLabel(disk: ManagedDisk, status: DiskStatus): string {
  const operation = status.operation;
  if (operation.status === 'running' && operation.label === disk.label) {
    if (operation.action === 'eject') return 'Unmounting…';
    if (operation.action === 'repair') return 'Repairing…';
    return 'Mounting…';
  }
  if (disk.error) return 'Error';
  if (disk.health.state === 'critical') return 'Health error';
  if (disk.health.state === 'warning') return 'Warning';
  if (!disk.attached) return 'No device';
  if (disk.mounted) return 'Mounted';
  if ((disk.holdRemainingSeconds ?? 0) > 0) {
    return `Unmounted · ${Math.ceil(disk.holdRemainingSeconds ?? 0)}s hold`;
  }
  return 'Unmounted';
}

export function diskTone(disk: ManagedDisk): StatusTone {
  if (disk.error || disk.health.state === 'critical') return 'bad';
  if (disk.health.state === 'warning' || (!disk.mounted && disk.attached)) return 'warning';
  if (disk.mounted) return 'good';
  return 'neutral';
}

export function diskOperationLabel(status: DiskStatus | null): string {
  if (!status) return 'No disk data';
  const operation = status.operation;
  if (operation.status === 'idle') return `Updated ${formatRelativeTime(status.checkedAt)}`;
  if (operation.status === 'running') {
    return `${operation.action ?? 'Disk action'} running for ${operation.label ?? 'disk'}`;
  }
  if (operation.status === 'error') {
    return operation.error ?? `${operation.label ?? 'Disk action'} failed`;
  }
  return `${operation.label ?? 'Disk action'} complete · ${formatRelativeTime(operation.completedAt)}`;
}
