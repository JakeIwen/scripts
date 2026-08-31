import type { PollingState } from '../../hooks/usePollingResource';
import { formatBatteryVoltage, type TelemetrySummary, voltageCheckLabel } from './telemetrySummary';
import { type TelemetryControls, useTelemetryControls } from './controls';
import { useTelemetrySummary } from './useTelemetrySummary';
import './telemetry.css';

export interface TelemetryTileViewProps {
  resource: PollingState<TelemetrySummary>;
  controls: TelemetryControls;
}

function observedLabel(observedAt: string | null): string {
  if (!observedAt) return 'Timestamp unavailable';
  const date = new Date(observedAt);
  if (Number.isNaN(date.getTime())) return 'Timestamp unavailable';
  return `Observed · ${date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  })}`;
}

function telemetryUrl(): string {
  const url = new URL(window.location.href);
  url.port = '8765';
  url.pathname = '/';
  url.search = '';
  url.hash = '';
  return url.toString();
}

export function TelemetryTileView({ resource, controls }: TelemetryTileViewProps) {
  const summary = resource.data;
  const serviceAvailable = summary?.service.available === true;
  const serviceRunning = summary?.service.running === true;
  const serviceLabel = controls.serviceBusy
    ? 'WAIT'
    : serviceAvailable
      ? serviceRunning
        ? 'UP'
        : 'DOWN'
      : 'NO DATA';
  const serviceActionLabel = serviceRunning ? 'Stop service' : 'Start service';
  const checkRunning = controls.voltageCheckBusy || summary?.check.status === 'running';
  const checkLabel = checkRunning ? 'Checking voltage…' : 'Check voltage now';
  const batteryAvailable = summary?.battery.available === true;
  const voltage = summary ? formatBatteryVoltage(summary.battery) : '—';
  const source = batteryAvailable
    ? summary?.battery.source === 'live'
      ? 'live'
      : summary?.battery.source === 'engine_off'
        ? 'engine-off passive'
        : 'last voltage_mon'
    : 'Battery voltage unavailable';
  const observed = batteryAvailable
    ? observedLabel(summary?.battery.observedAt ?? null)
    : 'No live or saved voltage reading';

  return (
    <section
      className="tile telemetry-tile"
      aria-labelledby="telemetry-tile-title"
      title={resource.error?.message}
    >
      <button
        type="button"
        className={`telemetry-tile__service ${
          serviceAvailable
            ? serviceRunning
              ? 'telemetry-tile__service--good'
              : 'telemetry-tile__service--bad'
            : ''
        }`.trim()}
        disabled={!serviceAvailable || controls.serviceBusy}
        aria-busy={controls.serviceBusy}
        aria-label={serviceActionLabel}
        title={
          serviceAvailable
            ? `${serviceRunning ? 'Stop' : 'Start'} telemetry service`
            : (summary?.service.error ?? 'Telemetry service status unavailable')
        }
        onClick={() => void controls.toggleService()}
      >
        {serviceLabel}
      </button>
      <a className="telemetry-tile__open" href={telemetryUrl()}>
        <span className="telemetry-tile__heading">
          <span className="telemetry-tile__icon" aria-hidden="true">
            📊
          </span>
          <span
            className="telemetry-tile__title"
            id="telemetry-tile-title"
            role="heading"
            aria-level={2}
          >
            Telemetry
          </span>
        </span>
        <span className="telemetry-tile__voltage">
          <strong>{voltage}</strong>
          <span>{source}</span>
        </span>
        <span className="telemetry-tile__observed">{observed}</span>
      </a>
      <button
        type="button"
        className="telemetry-tile__check"
        disabled={checkRunning}
        aria-busy={checkRunning}
        aria-label={checkLabel}
        onClick={() => void controls.checkVoltage()}
      >
        <span aria-hidden="true">↻</span>
        <span>{checkLabel}</span>
        {summary && (
          <span className="visually-hidden" aria-hidden="true">
            {voltageCheckLabel(summary.check)}
          </span>
        )}
      </button>
    </section>
  );
}

export function TelemetryTile() {
  const resource = useTelemetrySummary();
  const controls = useTelemetryControls(resource);
  return <TelemetryTileView resource={resource} controls={controls} />;
}
