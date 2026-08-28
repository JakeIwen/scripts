import type { PollingState } from '../../hooks/usePollingResource';
import { KeyValueList } from '../../components/KeyValueList';
import { StatusPill } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import { usbSummary, usbTone, usbUpdatedLabel } from './presentation';
import type { UsbStatus } from './types';
import './usb.css';

export interface UsbTileProps {
  resource: PollingState<UsbStatus>;
  onOpen: () => void;
}

export function UsbTile({ resource, onOpen }: UsbTileProps) {
  const status = resource.data;
  const tone = usbTone(status, resource.error);
  const connected = status?.inventory.presentDeviceCount;
  const pillLabel =
    connected === undefined ? (resource.error ? 'No data' : 'Loading') : `${connected} connected`;
  const items = [
    {
      label: 'Remembered missing',
      value: status ? status.inventory.unpluggedDeviceCount : '—',
    },
    {
      label: 'Port controls',
      value: status?.ports.loaded ? (status.ports.expired ? 'Expired' : 'Loaded') : 'Not loaded',
    },
    { label: 'Status', value: usbUpdatedLabel(status) },
  ];

  return (
    <Tile
      icon="🔌"
      title="USB Devices"
      summary={resource.error && !status ? resource.error.message : usbSummary(status)}
      status={<StatusPill tone={tone}>{pillLabel}</StatusPill>}
      tone={tone}
      onClick={onOpen}
      ariaLabel="Open USB device details"
      className="usb-tile"
    >
      <KeyValueList items={items} />
      <aside className="usb-read-only" aria-label="USB controls available">
        <strong>Controls enabled</strong>
        <span>Open to discover, switch, cycle, or recover guarded USB ports.</span>
      </aside>
    </Tile>
  );
}
