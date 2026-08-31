import { StatusPill } from '../../components/StatusPill';
import { ubntStatusLabel, ubntTone } from './presentation';
import { StarlinkControl, type StarlinkStatusResource } from './StarlinkControl';
import type { UbntWifiStatus } from './types';
import './ubnt.css';

export interface UbntTileProps {
  status: UbntWifiStatus | null;
  error: Error | null;
  refreshing: boolean;
  dashboardStatus?: StarlinkStatusResource;
  onOpen: () => void;
}

function radioDetail(status: UbntWifiStatus | null): string {
  if (!status) return '—';
  const parts = [
    status.state.signalDbm === null ? null : `${status.state.signalDbm} dBm`,
    status.state.ccqPercent === null ? null : `${status.state.ccqPercent.toFixed(1)}% CCQ`,
  ].filter((part): part is string => part !== null);
  return parts.join(' · ') || 'No radio measurements';
}

export function UbntTile({ status, error, refreshing, dashboardStatus, onOpen }: UbntTileProps) {
  const tone = ubntTone(status, error);
  const associated = status?.state.associatedSsid || status?.state.configuredSsid;
  const summary =
    status?.reachable === false
      ? 'No UBNT Ethernet response'
      : status?.reachable === true
        ? `${associated || 'Unknown SSID'} · ${status.state.associatedSsid ? radioDetail(status) : 'Not associated'}`
        : 'Waiting for antenna status…';

  return (
    <section className={`tile tile--${tone} ubnt-tile`} aria-labelledby="ubnt-tile-title">
      <button
        className="ubnt-tile__open"
        type="button"
        aria-label="Open UBNT Wi-Fi details"
        onClick={onOpen}
      />
      <header className="tile__header">
        <span className="tile__icon" aria-hidden="true">
          📡
        </span>
        <h2 className="tile__title" id="ubnt-tile-title">
          UBNT Wi-Fi
        </h2>
        <div className="tile__status">
          <StatusPill tone={tone}>{ubntStatusLabel(status, refreshing)}</StatusPill>
        </div>
      </header>
      <div className="tile__summary">
        <span
          className={`ubnt-tile__radio-dot${status?.reachable === true && status.state.associatedSsid ? ' ubnt-tile__radio-dot--good' : ''}`}
          aria-hidden="true"
        />
        <span>{summary}</span>
      </div>
      {dashboardStatus && <StarlinkControl resource={dashboardStatus} variant="tile" />}
    </section>
  );
}
