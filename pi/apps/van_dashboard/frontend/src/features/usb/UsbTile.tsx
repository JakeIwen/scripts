import type { PollingState } from '../../hooks/usePollingResource';
import type { UsbStatus } from './types';
import './usb.css';

export interface UsbTileProps {
  resource: PollingState<UsbStatus>;
  onOpen: () => void;
}

type UsbTileState = 'good' | 'warning' | 'unknown';

function tileState(status: UsbStatus | null): UsbTileState {
  if (!status || status.inventory.lastSuccessAt === null) return 'unknown';
  if (status.inventory.lastError || status.inventory.unpluggedDeviceCount > 0) return 'warning';
  return 'good';
}

function pillLabel(status: UsbStatus | null): string {
  if (!status || status.inventory.lastSuccessAt === null) return 'NO DATA';
  if (status.inventory.lastError) return 'STALE';
  if (status.inventory.unpluggedDeviceCount > 0) return 'CHANGE';
  return 'LIVE';
}

function summaryLabel(status: UsbStatus | null, loading: boolean): string {
  if (!status) return loading ? 'Reading live USB devices…' : 'USB status unavailable';
  if (status.inventory.lastSuccessAt === null) return 'USB status unavailable';
  const unplugged = status.inventory.unpluggedDeviceCount;
  if (unplugged > 0) return `${unplugged} unplugged since monitoring began`;
  const present = status.inventory.presentDeviceCount;
  return `${present} non-hub device${present === 1 ? '' : 's'} connected`;
}

export function UsbTile({ resource, onOpen }: UsbTileProps) {
  const status = resource.data;
  const state = tileState(status);
  const hasData = status?.inventory.lastSuccessAt !== null && status !== null;
  const connected = hasData ? String(status.inventory.presentDeviceCount) : '—';
  const storage = hasData
    ? status.inventory.storageLabels.length
      ? status.inventory.storageLabels.join(', ')
      : 'None detected'
    : '—';

  return (
    <button
      type="button"
      className={`tile usb-tile usb-tile--${state}`}
      onClick={onOpen}
      aria-label="Open USB device details"
      aria-haspopup="dialog"
    >
      <span className="usb-tile__pill">{pillLabel(status)}</span>
      <span className="usb-tile__heading">
        <span className="usb-tile__icon" aria-hidden="true">
          🔌
        </span>
        <span className="usb-tile__title" role="heading" aria-level={2}>
          USB Devices
        </span>
      </span>
      <span className="usb-tile__summary">{summaryLabel(status, !resource.error && !status)}</span>
      <span className="usb-tile__storage">
        <span className="usb-tile__storage-heading">
          Connected storage
          <span className="visually-hidden">; {connected} total USB devices</span>
        </span>
        <span className="usb-tile__storage-devices">{storage}</span>
      </span>
    </button>
  );
}
