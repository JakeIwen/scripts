import type { PollingState } from '../../hooks/usePollingResource';
import { BottomSheet } from '../../components/BottomSheet';
import { formatBytes, formatRelativeTime } from '../../utils/format';
import type { UsbControls } from '../usb/controls';
import type { UsbStatus } from '../usb/types';
import type { StorageControls } from './controls';
import type { StorageResources } from './hooks';
import { diskOperationLabel, diskStateLabel, diskTone, requestedPolicyState } from './presentation';
import { assessUsbReset } from './resetEligibility';
import type { DiskStatus, ManagedDisk, StoragePolicy } from './types';
import './storage.css';

export interface StorageSheetProps {
  open: boolean;
  onClose: () => void;
  resources: StorageResources;
  usbResource: PollingState<UsbStatus>;
  controls: StorageControls;
  usbControls: UsbControls;
}

function PolicyCard({
  label,
  detail,
  value,
  enabled,
  disabled,
  onToggle,
}: {
  label: string;
  detail: string;
  value: string;
  enabled: boolean;
  disabled: boolean;
  onToggle: () => void;
}) {
  const blocked = value.includes('blocked');
  const off = value === 'Off';
  return (
    <button
      type="button"
      className={`storage-policy-card ${blocked ? 'storage-policy-card--blocked' : ''} ${
        off ? 'storage-policy-card--off' : ''
      }`}
      disabled={disabled}
      aria-pressed={enabled}
      aria-label={label}
      onClick={onToggle}
    >
      <span>
        <strong>{label}</strong>
        <small>{detail}</small>
      </span>
      <b>{value}</b>
    </button>
  );
}

function PolicySection({
  policy,
  controls,
  diskOperationRunning,
}: {
  policy: StoragePolicy;
  controls: StorageControls;
  diskOperationRunning: boolean;
}) {
  const disabled = controls.blocked || diskOperationRunning;
  return (
    <>
      <div className="storage-runtime">
        <article>
          <span>Managed HDDs</span>
          <strong>{policy.runtime.disksMounted ? 'Mounted' : 'Unmounted'}</strong>
          <small>{policy.runtime.mountedDiskLabels.join(', ') || 'No managed HDD mounts'}</small>
        </article>
        <article>
          <span>qBittorrent</span>
          <strong>{policy.runtime.qbittorrentRunning ? 'Running' : 'Stopped'}</strong>
          <small>Authoritative process state</small>
        </article>
      </div>
      <div className="storage-policy-list">
        <PolicyCard
          label="HDDs enabled"
          detail="Keep rotational storage mounted while parked"
          value={requestedPolicyState(policy, 'disksEnabled')}
          enabled={policy.disksEnabled}
          disabled={disabled}
          onToggle={() => void controls.setPolicy('disks_enabled', !policy.disksEnabled)}
        />
        <PolicyCard
          label="Torrents enabled"
          detail="Requires HDDs enabled"
          value={requestedPolicyState(policy, 'torrentsEnabled')}
          enabled={policy.torrentsEnabled}
          disabled={disabled}
          onToggle={() => void controls.setPolicy('torrents_enabled', !policy.torrentsEnabled)}
        />
        <PolicyCard
          label="Allow torrents on Starlink"
          detail="Requires both HDDs and torrents enabled"
          value={requestedPolicyState(policy, 'allowStarlinkTorrents')}
          enabled={policy.allowStarlinkTorrents}
          disabled={disabled}
          onToggle={() =>
            void controls.setPolicy('allow_starlink_torrents', !policy.allowStarlinkTorrents)
          }
        />
      </div>
    </>
  );
}

function DiskCard({
  disk,
  status,
  policy,
  usb,
  controls,
  usbControls,
}: {
  disk: ManagedDisk;
  status: DiskStatus;
  policy: StoragePolicy | null;
  usb: UsbStatus | null;
  controls: StorageControls;
  usbControls: UsbControls;
}) {
  const identity = [
    disk.sizeBytes === null ? null : formatBytes(disk.sizeBytes),
    disk.filesystem,
    disk.mounted ? disk.expectedMount : null,
  ].filter((value): value is string => Boolean(value));
  const reset = assessUsbReset(disk, status.operation, usb);
  const healthDetail = disk.error ?? disk.health.currentErrorMessage ?? disk.health.message;
  const operationRunning = status.operation.status === 'running';
  const baseDisabled =
    controls.blocked ||
    usbControls.blocked ||
    operationRunning ||
    !disk.controllable ||
    !disk.attached ||
    Boolean(disk.error);
  const primaryAction = disk.mounted ? 'eject' : 'mount';
  const mountBlocked =
    primaryAction === 'mount' && disk.requiresDiskPolicy && policy?.disksEnabled !== true;
  const repairDisabled = baseDisabled || !disk.health.repairable;
  const resetDisabled = baseDisabled || !reset.eligible || reset.port === null;

  return (
    <article className={`storage-disk storage-disk--${diskTone(disk)}`}>
      <header>
        <div>
          <h4>{disk.label}</h4>
          <p>{identity.join(' · ') || disk.expectedMount}</p>
        </div>
        <span>{diskStateLabel(disk, status)}</span>
      </header>
      <dl>
        <div>
          <dt>Current observation</dt>
          <dd>{disk.health.observation}</dd>
        </div>
        <div>
          <dt>Health evidence</dt>
          <dd>{healthDetail}</dd>
        </div>
        <div>
          <dt>Last offline check</dt>
          <dd>{disk.health.checkedAt ? formatRelativeTime(disk.health.checkedAt) : 'Never'}</dd>
        </div>
        <div>
          <dt>USB reset</dt>
          <dd>{reset.label}</dd>
        </div>
      </dl>
      <p className={`storage-reset storage-reset--${reset.tone}`}>{reset.detail}</p>
      {disk.controllable && (
        <div className="storage-disk__actions">
          <button
            className="secondary-button"
            type="button"
            disabled={baseDisabled || mountBlocked}
            onClick={() => void controls.runDiskAction(disk, primaryAction)}
          >
            {primaryAction === 'eject' ? 'Unmount' : 'Mount'}
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={resetDisabled}
            onClick={() => {
              if (reset.port) void usbControls.runPortAction(reset.port, 'cycle');
            }}
          >
            Reset USB
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={repairDisabled}
            onClick={() => void controls.runDiskAction(disk, 'repair')}
          >
            Repair
          </button>
        </div>
      )}
    </article>
  );
}

export function StorageSheet({
  open,
  onClose,
  resources,
  usbResource,
  controls,
  usbControls,
}: StorageSheetProps) {
  const policy = resources.policy.data;
  const disks = resources.disks.data;
  return (
    <BottomSheet
      open={open}
      title="Disks & Torrents"
      description="Requested policy, authoritative runtime state, and disk health evidence."
      onClose={onClose}
    >
      <header className="storage-sheet__summary">
        <span>{diskOperationLabel(disks)}</span>
        <button
          className="secondary-button"
          type="button"
          disabled={
            resources.policy.refreshing || resources.disks.refreshing || controls.running || !open
          }
          onClick={() => {
            void resources.policy.refresh();
            void resources.disks.refresh();
          }}
        >
          {resources.policy.refreshing || resources.disks.refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </header>
      {(resources.policy.error || resources.disks.error) && (
        <p className="error-message">
          {resources.policy.error?.message ?? resources.disks.error?.message}
        </p>
      )}
      {(controls.lastError || controls.lastMessage) && (
        <p className={controls.lastError ? 'error-message' : 'storage-control-message'}>
          {controls.lastError ?? controls.lastMessage}
        </p>
      )}

      <section className="storage-sheet__section" aria-labelledby="storage-policy-title">
        <div className="storage-sheet__heading">
          <div>
            <h3 id="storage-policy-title">Policy & runtime</h3>
            <p>Requested switches can remain on while safety conditions block runtime state.</p>
          </div>
          <span>{controls.blocked ? 'Controls blocked pending status' : 'Desired state'}</span>
        </div>
        {policy ? (
          <PolicySection
            policy={policy}
            controls={controls}
            diskOperationRunning={disks?.operation.status === 'running'}
          />
        ) : (
          <p className="storage-empty">Loading policy…</p>
        )}
      </section>

      <section className="storage-sheet__section" aria-labelledby="storage-disks-title">
        <div className="storage-sheet__heading">
          <div>
            <h3 id="storage-disks-title">USB disks</h3>
            <p>Reset explanations combine disk state with the shared, cached USB port snapshot.</p>
          </div>
          <span>{usbResource.error ? 'USB status unavailable' : 'Shared USB status'}</span>
        </div>
        <div className="storage-disk-list">
          {disks?.disks.length ? (
            disks.disks.map((disk) => (
              <DiskCard
                disk={disk}
                key={disk.label}
                status={disks}
                policy={policy}
                usb={usbResource.error ? null : usbResource.data}
                controls={controls}
                usbControls={usbControls}
              />
            ))
          ) : (
            <p className="storage-empty">No configured disk labels were returned.</p>
          )}
        </div>
      </section>
    </BottomSheet>
  );
}
