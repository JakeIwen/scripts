import { KeyValueList } from '../../components/KeyValueList';
import { StatusPill } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import { formatPercent } from '../../utils/format';
import { systemHealthStatusLabel, systemHealthTone } from './presentation';
import type { SystemHealthReport } from './types';
import './systemHealth.css';

export interface SystemHealthTileProps {
  report: SystemHealthReport | null;
  error: Error | null;
  refreshing: boolean;
  onOpen: () => void;
}

function temperature(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${value.toFixed(1)} °C`;
}

export function SystemHealthTile({ report, error, refreshing, onOpen }: SystemHealthTileProps) {
  const tone = systemHealthTone(report, error);
  const current = report?.current;
  const evidence = report?.evidence;
  const power = evidence?.undervoltageEpisodes
    ? `${evidence.undervoltageEpisodes} drop${evidence.undervoltageEpisodes === 1 ? '' : 's'}`
    : report
      ? 'No events in range'
      : '—';
  const items = [
    { label: 'Power', value: power },
    { label: 'CPU peak', value: formatPercent(report?.peaks.cpuPercent.value) },
    {
      label: 'CPU / SoC temp',
      value: temperature(current?.temperatureCelsius ?? report?.peaks.temperatureCelsius.value),
    },
    { label: 'Memory peak', value: formatPercent(report?.peaks.memoryPercent.value) },
    {
      label: 'Throttling',
      value: current?.activeThrottleFlags.length
        ? current.activeThrottleFlags.join(', ')
        : report
          ? 'Clear now'
          : '—',
    },
  ];

  return (
    <Tile
      icon="⚡"
      title="System Health"
      summary={
        error && !report
          ? error.message
          : (report?.headline ?? 'Loading passive power and resource evidence…')
      }
      status={
        <StatusPill tone={tone}>{systemHealthStatusLabel(report, error, refreshing)}</StatusPill>
      }
      tone={tone}
      onClick={onOpen}
      ariaLabel="Open system health details"
      className="system-health-tile"
    >
      <KeyValueList items={items} />
      <p className="system-health-tile__read-only">Passive monitoring · read-only</p>
    </Tile>
  );
}
