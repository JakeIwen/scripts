import { KeyValueList } from '../../components/KeyValueList';
import { StatusPill } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import type { StorageResources } from './hooks';
import { requestedPolicyState, storageSummary, storageTone } from './presentation';
import './storage.css';

export interface StorageTileProps {
  resources: StorageResources;
  onOpen: () => void;
}

export function StorageTile({ resources, onOpen }: StorageTileProps) {
  const policy = resources.policy.data;
  const disks = resources.disks.data;
  const error = resources.policy.error ?? resources.disks.error;
  const tone = storageTone(policy, disks, error);
  const mountedCount = disks?.disks.filter((disk) => disk.mounted).length;
  const items = policy
    ? [
        { label: 'HDD policy', value: requestedPolicyState(policy, 'disksEnabled') },
        { label: 'Torrent policy', value: requestedPolicyState(policy, 'torrentsEnabled') },
        {
          label: 'Runtime',
          value: `${policy.runtime.mountedDiskLabels.length} mounted · torrents ${
            policy.runtime.qbittorrentRunning ? 'running' : 'stopped'
          }`,
        },
      ]
    : [
        { label: 'HDD policy', value: '—' },
        { label: 'Torrent policy', value: '—' },
        { label: 'Runtime', value: '—' },
      ];

  return (
    <Tile
      icon="💾"
      title="Disk & Torrent"
      summary={error && !policy ? error.message : storageSummary(policy)}
      status={
        <StatusPill tone={tone}>
          {mountedCount === undefined ? 'Loading' : `${mountedCount} mounted`}
        </StatusPill>
      }
      tone={tone}
      onClick={onOpen}
      ariaLabel="Open disk and torrent details"
      className="storage-tile"
    >
      <KeyValueList items={items} />
      <aside className="storage-read-only" aria-label="Storage controls available">
        <strong>Controls enabled</strong>
        <span>Open for policy, mount, unmount, repair, and USB reset controls.</span>
      </aside>
    </Tile>
  );
}
