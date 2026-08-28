import { KeyValueList } from '../../components/KeyValueList';
import { StatusPill } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import { ubntStatusLabel, ubntTone } from './presentation';
import type { UbntWifiStatus } from './types';
import './ubnt.css';

export interface UbntTileProps {
  status: UbntWifiStatus | null;
  error: Error | null;
  refreshing: boolean;
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

function selectorLabel(status: UbntWifiStatus | null): string {
  if (status?.state.automaticPaused === true) return 'Paused';
  if (status?.state.automaticPaused === false) {
    return status.state.selectorRunning === false ? 'Automatic · idle' : 'Automatic';
  }
  return '—';
}

export function UbntTile({ status, error, refreshing, onOpen }: UbntTileProps) {
  const tone = ubntTone(status, error);
  const associated = status?.state.associatedSsid || status?.state.configuredSsid;
  const summary =
    error && !status
      ? error.message
      : status?.reachable === false
        ? 'No UBNT Ethernet response'
        : associated
          ? `${associated} · ${radioDetail(status)}`
          : 'Waiting for antenna association…';
  const items = [
    {
      label: 'Antenna',
      value:
        status?.reachable === true ? 'Reachable' : status?.reachable === false ? 'Offline' : '—',
    },
    { label: 'Radio', value: radioDetail(status) },
    { label: 'Selection', value: selectorLabel(status) },
    { label: 'Saved networks', value: status ? String(status.profiles.length) : '—' },
  ];

  return (
    <Tile
      icon="📡"
      title="UBNT Wi-Fi"
      summary={summary}
      status={<StatusPill tone={tone}>{ubntStatusLabel(status, refreshing)}</StatusPill>}
      tone={tone}
      onClick={onOpen}
      ariaLabel="Open UBNT Wi-Fi details"
      className="ubnt-tile"
    >
      <KeyValueList items={items} />
      <p className="ubnt-tile__read-only">Open details to manage antenna Wi-Fi</p>
    </Tile>
  );
}
