import { KeyValueList } from '../../components/KeyValueList';
import { StatusPill } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import { formatBytes, formatPercent } from '../../utils/format';
import { systemHealthRangeLabel, systemHealthStatusLabel, systemHealthTone } from './presentation';
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

function rate(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${formatBytes(value)}/s`;
}

function throttleLabel(report: SystemHealthReport | null): string {
  const current = report?.current;
  if (current?.activeThrottleFlags.length) {
    return `ACTIVE · ${current.activeThrottleFlags.map((flag) => flag.replaceAll('_', ' ')).join(', ')}`;
  }
  if (current?.occurredThrottleFlags.length) return 'Clear now · seen this boot';
  if (current?.throttleWord !== null && current?.throttleWord !== undefined) return 'Clear';
  return report ? 'No data' : '—';
}

export function SystemHealthTile({ report, error, refreshing, onOpen }: SystemHealthTileProps) {
  const tone = systemHealthTone(report, error);
  const current = report?.current;
  const evidence = report?.evidence;
  const power = evidence?.undervoltageEpisodes
    ? `${evidence.undervoltageEpisodes} drop${evidence.undervoltageEpisodes === 1 ? '' : 's'} · ${evidence.undervoltageSeconds.toFixed(1)}s`
    : current?.occurredThrottleFlags.includes('under_voltage')
      ? 'Sticky history set'
      : report
        ? `No events in range (${systemHealthRangeLabel(report.rangeHours)})`
        : error
          ? '—'
          : 'Checking…';
  const items = [
    { label: 'Power', value: power },
    { label: 'CPU peak', value: formatPercent(report?.peaks.cpuPercent.value, 1) },
    {
      label: 'CPU / SoC temp',
      value: temperature(current?.temperatureCelsius ?? report?.peaks.temperatureCelsius.value),
    },
    {
      label: 'Throttling',
      value: report ? throttleLabel(report) : error ? '—' : 'Checking…',
    },
    { label: 'Memory peak', value: formatPercent(report?.peaks.memoryPercent.value, 1) },
    {
      label: 'Network',
      value: report
        ? `↓ ${rate(current?.networkReceiveBytesPerSecond)} · ↑ ${rate(current?.networkTransmitBytesPerSecond)}`
        : '—',
    },
    {
      label: 'Disk I/O',
      value: report
        ? `R ${rate(current?.diskReadBytesPerSecond)} · W ${rate(current?.diskWriteBytesPerSecond)}`
        : '—',
    },
  ];

  return (
    <Tile
      icon="⚡"
      title="System Health"
      summary={
        error && !report
          ? 'System monitor unavailable'
          : report?.stale
            ? 'Monitor data is stale'
            : (report?.headline ?? 'Loading power and resource history…')
      }
      status={
        <StatusPill tone={tone} dot={false}>
          {systemHealthStatusLabel(report, error, refreshing)}
        </StatusPill>
      }
      tone={tone}
      onClick={onOpen}
      ariaLabel="Open system health details"
      className="system-health-tile"
    >
      <KeyValueList items={items} />
    </Tile>
  );
}
