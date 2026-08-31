import type { StorageResources } from './hooks';
import { storageSummary } from './presentation';
import './storage.css';

export interface StorageTileProps {
  resources: StorageResources;
  onOpen: () => void;
}

export function StorageTile({ resources, onOpen }: StorageTileProps) {
  const policy = resources.policy.data;
  const error = resources.policy.error ?? resources.disks.error;
  const summary = error && !policy ? 'Runtime state unavailable' : storageSummary(policy);

  return (
    <button
      type="button"
      className="tile storage-tile"
      onClick={onOpen}
      aria-label="Open disk and torrent details"
      aria-haspopup="dialog"
    >
      <span className="storage-tile__heading">
        <span className="storage-tile__icon" aria-hidden="true">
          💾
        </span>
        <span className="storage-tile__title" role="heading" aria-level={2}>
          Disk &amp; Torrent
        </span>
      </span>
      <span className="storage-tile__summary">{summary}</span>
    </button>
  );
}
