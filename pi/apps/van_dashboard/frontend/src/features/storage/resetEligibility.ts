import type { UsbPort, UsbStatus } from '../usb/types';
import type { DiskOperation, ManagedDisk, UsbResetAssessment } from './types';

function uniquelyMappedPowerPort(label: string, usb: UsbStatus): UsbPort | null {
  const matches = new Map<string, UsbPort>();
  for (const hub of usb.ports.hubs) {
    for (const port of hub.ports) {
      if (
        port.method === 'power' &&
        port.storageLabels.length === 1 &&
        port.storageLabels[0] === label
      ) {
        matches.set(port.key, port);
      }
    }
  }
  return matches.size === 1 ? ([...matches.values()][0] ?? null) : null;
}

function blocked(label: string, detail: string, port: UsbPort | null = null): UsbResetAssessment {
  return { eligible: false, tone: 'neutral', label, detail, port };
}

/** Explain exactly why the shared USB reset control is enabled or blocked. */
export function assessUsbReset(
  disk: ManagedDisk,
  diskOperation: DiskOperation,
  usb: UsbStatus | null,
): UsbResetAssessment {
  if (!disk.attached) {
    return blocked('Not applicable', 'The disk is not attached.');
  }
  if (!usb) {
    return blocked('USB status unavailable', 'Waiting for the shared USB inventory.');
  }
  if (usb.inventory.lastError) {
    return blocked(
      'USB inventory stale',
      `Current mounted storage cannot be verified: ${usb.inventory.lastError}`,
    );
  }
  if (!usb.ports.loaded) {
    return blocked(
      'Controls not loaded',
      'Open USB Devices and load port controls to identify an independently powered port.',
    );
  }
  if (usb.ports.expired) {
    return blocked('Controls expired', 'Open USB Devices and reload port controls.');
  }
  if (usb.ports.lastError) {
    return blocked('Discovery incomplete', usb.ports.lastError);
  }

  const port = uniquelyMappedPowerPort(disk.label, usb);
  if (!port) {
    return blocked(
      'No unique power port',
      'No single independently powered USB port maps only to this disk.',
    );
  }
  if (diskOperation.status === 'running') {
    return blocked('Disk operation running', 'Wait for the current disk action to finish.', port);
  }
  if (usb.ports.operation.status === 'running') {
    return blocked('USB operation running', 'Wait for the current USB action to finish.', port);
  }
  if (disk.mounted) {
    return blocked('Unmount first', `${disk.label} must be safely unmounted before reset.`, port);
  }
  if (port.mountedLabels.length > 0) {
    return blocked(
      'Mounted storage present',
      `The port still reports mounted storage: ${port.mountedLabels.join(', ')}.`,
      port,
    );
  }
  if (port.enabled === false) {
    return blocked('Port is off', 'The mapped USB port is already disabled.', port);
  }
  return {
    eligible: true,
    tone: 'good',
    label: 'Eligible',
    detail: `Mapped uniquely to ${port.key}; the disk is unmounted and no mounted labels remain.`,
    port,
  };
}
