import type { PollingState } from '../../hooks/usePollingResource';
import { BottomSheet } from '../../components/BottomSheet';
import { formatRelativeTime } from '../../utils/format';
import type { UsbControls } from './controls';
import { usbDeviceEventLabel, usbOperationLabel, usbUpdatedLabel } from './presentation';
import type { UsbDevice, UsbHub, UsbPort, UsbStatus } from './types';
import './usb.css';

export interface UsbSheetProps {
  open: boolean;
  onClose: () => void;
  resource: PollingState<UsbStatus>;
  controls: UsbControls;
}

function DeviceRow({ device }: { device: UsbDevice }) {
  const event = usbDeviceEventLabel(device);
  const count =
    device.maxCount > 1
      ? `${device.presentCount}/${device.maxCount}`
      : device.presentCount > 0
        ? 'Connected'
        : 'Missing';
  const routes = device.knownInstances.map((instance) => instance.location);

  return (
    <article className={`usb-device usb-device--${device.status}`}>
      <span className="usb-device__dot" aria-hidden="true" />
      <div className="usb-device__identity">
        <strong>{device.description}</strong>
        <small>
          Bus {device.bus} · ID {device.deviceId}
        </small>
        {routes.length > 0 && <small>Last known USB · {routes.join(' · ')}</small>}
        {device.labels.length > 0 && (
          <div className="usb-labels">
            {device.labels.map((label) => (
              <span key={label}>{label}</span>
            ))}
          </div>
        )}
        {event && <time>{event}</time>}
      </div>
      <span className="usb-device__count">{count}</span>
    </article>
  );
}

function PortCard({
  port,
  hubName,
  controls,
  controlsUnavailable,
  operationRunning,
}: {
  port: UsbPort;
  hubName: string;
  controls: UsbControls;
  controlsUnavailable: boolean;
  operationRunning: boolean;
}) {
  const state = port.enabled === null ? 'Unknown' : port.enabled ? 'On' : 'Off';
  const enabled = port.enabled !== false;
  const busy = controls.blocked || controlsUnavailable || operationRunning;
  const disconnectBlocked = port.mountedLabels.length > 0;
  const details = [
    ...port.deviceDescriptions,
    port.downstreamDeviceCount > port.deviceDescriptions.length
      ? `${port.downstreamDeviceCount} downstream devices`
      : null,
    port.storageLabels.length > 0 ? `Storage: ${port.storageLabels.join(', ')}` : null,
    port.mountedLabels.length > 0 ? `Mounted: ${port.mountedLabels.join(', ')}` : null,
  ].filter((value): value is string => Boolean(value));

  return (
    <article className="usb-port">
      <header>
        <strong>
          {hubName} · port {port.port}
        </strong>
        <span className={`usb-port__state usb-port__state--${state.toLowerCase()}`}>{state}</span>
      </header>
      <p>{details.join(' · ') || 'No downstream device reported'}</p>
      <small>{port.method === 'power' ? 'Power + data control' : 'Data-only control'}</small>
      {disconnectBlocked && (
        <p className="usb-port__mounted">
          Unmount {port.mountedLabels.join(', ')} before disabling or cycling this port.
        </p>
      )}
      <div className="usb-port__actions">
        <button
          className="secondary-button"
          type="button"
          disabled={busy || enabled}
          onClick={() => void controls.runPortAction(port, 'on')}
        >
          Enable
        </button>
        <button
          className="secondary-button"
          type="button"
          disabled={busy || !enabled || disconnectBlocked}
          onClick={() => void controls.runPortAction(port, 'off')}
        >
          Disable
        </button>
        <button
          className="secondary-button"
          type="button"
          disabled={busy || !enabled || disconnectBlocked}
          onClick={() => void controls.runPortAction(port, 'cycle')}
        >
          Cycle
        </button>
      </div>
    </article>
  );
}

function HubCard({
  hub,
  controls,
  controlsUnavailable,
  operationRunning,
}: {
  hub: UsbHub;
  controls: UsbControls;
  controlsUnavailable: boolean;
  operationRunning: boolean;
}) {
  return (
    <section className="usb-hub">
      <header className="usb-hub__header">
        <div>
          <h4>{hub.description}</h4>
          <p>{hub.detail ?? `Location ${hub.location}`}</p>
        </div>
        <span>{hub.physical ? 'Physical hub' : hub.advanced ? 'Advanced' : hub.method}</span>
      </header>
      <div className="usb-port-grid">
        {hub.ports.map((port) => (
          <PortCard
            hubName={hub.description}
            key={port.key}
            port={port}
            controls={controls}
            controlsUnavailable={controlsUnavailable}
            operationRunning={operationRunning}
          />
        ))}
      </div>
    </section>
  );
}

function PortControls({ status, controls }: { status: UsbStatus; controls: UsbControls }) {
  const ports = status.ports;
  if (!ports.loaded) {
    return (
      <div className="usb-controls-empty">
        <strong>Port controls are not loaded</strong>
        <p>
          Discovery is explicit because it queries the live hub topology. Load controls only when
          you need to inspect or operate individual ports.
        </p>
        <div className="usb-port-toolbar">
          <button
            className="secondary-button"
            type="button"
            disabled={controls.blocked}
            onClick={() => void controls.discover()}
          >
            Load port controls
          </button>
          <button
            className="danger-button"
            type="button"
            disabled={controls.blocked}
            onClick={() => void controls.recoverUsb2()}
          >
            Recover USB 2
          </button>
        </div>
      </div>
    );
  }

  return (
    <>
      {(ports.expired || ports.lastError) && (
        <p className="error-message">
          {ports.lastError ?? 'Port controls have expired; refresh USB details to reload them.'}
        </p>
      )}
      <div className="usb-port-toolbar">
        <button
          className="secondary-button"
          type="button"
          disabled={controls.blocked || ports.operation.status === 'running'}
          onClick={() => void controls.discover()}
        >
          {ports.expired || ports.lastError ? 'Reload controls' : 'Refresh controls'}
        </button>
        <button
          className="danger-button"
          type="button"
          disabled={controls.blocked || ports.operation.status === 'running'}
          onClick={() => void controls.recoverUsb2()}
        >
          Recover USB 2
        </button>
      </div>
      <p className="usb-port-operation">{usbOperationLabel(ports.operation)}</p>
      <div className="usb-hub-list">
        {ports.hubs.map((hub) => (
          <HubCard
            hub={hub}
            key={hub.location}
            controls={controls}
            controlsUnavailable={ports.expired || Boolean(ports.lastError)}
            operationRunning={ports.operation.status === 'running'}
          />
        ))}
      </div>
    </>
  );
}

export function UsbSheet({ open, onClose, resource, controls }: UsbSheetProps) {
  const status = resource.data;
  return (
    <BottomSheet
      open={open}
      title="USB Devices"
      description="Remembered USB inventory and guarded live port controls."
      onClose={onClose}
    >
      <header className="usb-sheet__summary">
        <span>{usbUpdatedLabel(status)}</span>
        <button
          className="secondary-button"
          type="button"
          disabled={resource.refreshing || controls.running || !open}
          onClick={() => void resource.refresh()}
        >
          {resource.refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </header>
      {resource.error && <p className="error-message">{resource.error.message}</p>}
      {(controls.lastError || controls.lastMessage) && (
        <p className={controls.lastError ? 'error-message' : 'usb-control-message'}>
          {controls.lastError ?? controls.lastMessage}
        </p>
      )}

      <section className="usb-sheet__section" aria-labelledby="usb-port-controls-title">
        <div className="usb-sheet__heading">
          <div>
            <h3 id="usb-port-controls-title">Port controls</h3>
            <p>Mounted storage blocks disconnecting actions; the backend verifies it again.</p>
          </div>
          {status?.ports.checkedAt && (
            <span>Loaded {formatRelativeTime(status.ports.checkedAt)}</span>
          )}
        </div>
        {status ? (
          <PortControls status={status} controls={controls} />
        ) : (
          <p className="usb-controls-empty">Loading USB state…</p>
        )}
      </section>

      <section className="usb-sheet__section" aria-labelledby="usb-inventory-title">
        <div className="usb-sheet__heading">
          <div>
            <h3 id="usb-inventory-title">Remembered devices</h3>
            <p>Missing devices stay visible with their last-known topology and labels.</p>
          </div>
          <span>{status ? `${status.inventory.presentDeviceCount} connected` : '—'}</span>
        </div>
        <div className="usb-device-list" aria-busy={resource.initialLoading || resource.refreshing}>
          {status?.inventory.devices.length ? (
            status.inventory.devices.map((device) => (
              <DeviceRow
                device={device}
                key={`${device.bus}:${device.deviceId}:${device.description}`}
              />
            ))
          ) : (
            <p className="usb-controls-empty">No USB devices have been reported.</p>
          )}
        </div>
      </section>
    </BottomSheet>
  );
}
