import type { StatusTone } from '../../components/StatusPill';
import { formatRelativeTime } from '../../utils/format';
import type { UsbDevice, UsbPortOperation, UsbStatus } from './types';

export function usbTone(status: UsbStatus | null, error: Error | null): StatusTone {
  if (!status) return error ? 'bad' : 'neutral';
  if (status.inventory.lastError || error) return 'warning';
  if (status.ports.operation.status === 'error') return 'warning';
  if (status.inventory.unpluggedDeviceCount > 0) return 'warning';
  return 'good';
}

export function usbSummary(status: UsbStatus | null): string {
  if (!status) return 'Reading attached USB devices';
  const connected = status.inventory.presentDeviceCount;
  const storage = status.inventory.storageLabels;
  const deviceLabel = `${connected} non-hub device${connected === 1 ? '' : 's'} connected`;
  return storage.length ? `${deviceLabel} · ${storage.join(', ')}` : deviceLabel;
}

export function usbUpdatedLabel(status: UsbStatus | null): string {
  if (!status) return 'No data';
  if (status.inventory.lastError) return `Stale · ${status.inventory.lastError}`;
  return status.inventory.lastSuccessAt === null
    ? 'No successful sample'
    : `Updated ${formatRelativeTime(status.inventory.lastSuccessAt)}`;
}

export function usbDeviceEventLabel(device: UsbDevice): string | null {
  if (!device.event) return null;
  const label =
    device.event.kind === 'unplugged'
      ? 'Unplugged'
      : device.event.kind === 'replugged'
        ? 'Replugged'
        : 'Plugged';
  return `${label} ${formatRelativeTime(device.event.at)}`;
}

export function usbOperationLabel(operation: UsbPortOperation): string {
  if (operation.status === 'idle') return 'No port operation';
  const subject = operation.key ?? 'USB port';
  const action = operation.action ?? 'operation';
  if (operation.status === 'running') return `${subject} · ${action} running`;
  if (operation.status === 'error') {
    return `${subject} · ${operation.error ?? `${action} failed`}`;
  }
  return `${subject} · ${operation.message ?? `${action} complete`}`;
}
