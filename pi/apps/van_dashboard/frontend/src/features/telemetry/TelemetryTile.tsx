import type { PollingState } from '../../hooks/usePollingResource';
import { KeyValueList } from '../../components/KeyValueList';
import { StatusPill, type StatusTone } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import {
  batterySourceLabel,
  formatBatteryVoltage,
  type TelemetrySummary,
  voltageCheckLabel,
} from './telemetrySummary';
import { type TelemetryControls, useTelemetryControls } from './controls';
import { useTelemetrySummary } from './useTelemetrySummary';
import './telemetry.css';

export interface TelemetryTileViewProps {
  resource: PollingState<TelemetrySummary>;
  controls: TelemetryControls;
}

interface ServicePresentation {
  label: string;
  detail: string;
  tone: StatusTone;
}

function siblingServiceUrl(port: number): string {
  const url = new URL(window.location.href);
  url.port = String(port);
  url.pathname = '/';
  url.search = '';
  url.hash = '';
  return url.toString();
}

function describeService(summary: TelemetrySummary): ServicePresentation {
  if (!summary.service.available) {
    return {
      label: 'No data',
      detail: summary.service.error ?? 'Service status unavailable',
      tone: 'bad',
    };
  }
  if (summary.service.running) {
    return { label: 'Up', detail: 'Running', tone: 'good' };
  }
  return { label: 'Down', detail: 'Stopped', tone: 'bad' };
}

function observedLabel(observedAt: string | null): string {
  if (!observedAt) return 'Unavailable';
  const date = new Date(observedAt);
  if (Number.isNaN(date.getTime())) return 'Invalid timestamp';
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  });
}

function TelemetryControlPanel({
  summary,
  controls,
}: {
  summary: TelemetrySummary | null;
  controls: TelemetryControls;
}) {
  const serviceAvailable = summary?.service.available === true;
  const serviceRunning = summary?.service.running === true;
  const checkRunning = summary?.check.status === 'running';

  return (
    <section className="telemetry-tile__controls" aria-label="Telemetry controls">
      <button
        type="button"
        className="secondary-button"
        disabled={!serviceAvailable || controls.serviceBusy}
        aria-busy={controls.serviceBusy}
        onClick={() => void controls.toggleService()}
      >
        {controls.serviceBusy
          ? 'Changing service…'
          : serviceRunning
            ? 'Stop service'
            : 'Start service'}
      </button>
      <button
        type="button"
        className="telemetry-tile__check"
        disabled={controls.voltageCheckBusy || checkRunning}
        aria-busy={controls.voltageCheckBusy || checkRunning}
        onClick={() => void controls.checkVoltage()}
      >
        {controls.voltageCheckBusy || checkRunning ? 'Checking voltage…' : 'Check voltage now'}
      </button>
    </section>
  );
}

function TelemetryLink() {
  return (
    <a className="telemetry-tile__open" href={siblingServiceUrl(8765)}>
      Open telemetry dashboard
    </a>
  );
}

export function TelemetryTileView({ resource, controls }: TelemetryTileViewProps) {
  const summary = resource.data;
  if (!summary) {
    const failed = resource.error !== null;
    return (
      <Tile
        icon="📊"
        title="Telemetry"
        summary={failed ? resource.error?.message : 'Checking battery voltage and service state…'}
        status={
          <StatusPill tone={failed ? 'bad' : 'neutral'}>
            {failed ? 'No data' : 'Loading'}
          </StatusPill>
        }
        tone={failed ? 'bad' : 'neutral'}
        className="telemetry-tile"
      >
        <TelemetryControlPanel summary={null} controls={controls} />
        <TelemetryLink />
      </Tile>
    );
  }

  const service = describeService(summary);
  const voltage = formatBatteryVoltage(summary.battery);
  const source = batterySourceLabel(summary.battery.source);
  const summaryContent = (
    <span className="telemetry-tile__reading">
      <strong>{voltage}</strong>
      <span>{summary.battery.available ? source : summary.battery.detail}</span>
    </span>
  );
  const items = [
    { label: 'Service', value: service.detail },
    { label: 'Voltage check', value: voltageCheckLabel(summary.check) },
    { label: 'Observed', value: observedLabel(summary.battery.observedAt) },
  ];

  return (
    <Tile
      icon="📊"
      title="Telemetry"
      summary={summaryContent}
      status={<StatusPill tone={service.tone}>{service.label}</StatusPill>}
      className="telemetry-tile"
    >
      <KeyValueList items={items} className="telemetry-tile__details" />
      {resource.error && (
        <p className="telemetry-tile__stale-error">Refresh failed · {resource.error.message}</p>
      )}
      <TelemetryControlPanel summary={summary} controls={controls} />
      <TelemetryLink />
    </Tile>
  );
}

export function TelemetryTile() {
  const resource = useTelemetrySummary();
  const controls = useTelemetryControls(resource);
  return <TelemetryTileView resource={resource} controls={controls} />;
}
