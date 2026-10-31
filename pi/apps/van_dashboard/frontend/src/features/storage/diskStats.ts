import type { ChartPoint } from '../../components/charts/TimeSeriesChart';
import type { DiskHistory, DiskStatsEntry, SmartReading } from './types';

export const DISK_STATS_POLL_INTERVAL_MS = 60_000;
export const DISK_HISTORY_REFRESH_MS = 60_000;
// smart_watch.sh reads mounted HDDs every 10 minutes and alerts at 60C, the
// rated maximum of these drives (pi/scripts/smart_watch.sh).
export const SMART_INTERVAL_SECONDS = 600;
export const TEMPERATURE_LIMIT_C = 60;

export const HISTORY_RANGES = [
  { hours: 6, label: '6 hours' },
  { hours: 24, label: '24 hours' },
  { hours: 168, label: '7 days' },
  { hours: 720, label: '30 days' },
] as const;
export type HistoryHours = (typeof HISTORY_RANGES)[number]['hours'];
export const DEFAULT_HISTORY_HOURS: HistoryHours = 24;

/** The range a history response covers, which trails the selected range until it arrives. */
export function historyHours(history: DiskHistory): number {
  return Math.round((history.until - history.since) / 3600);
}

/** A y-range padded to whole 5 °C steps that always shows the alert limit. */
export function temperatureAxis(temperature: { low: number; high: number }) {
  return {
    yMin: Math.floor((temperature.low - 5) / 5) * 5,
    yMax: Math.max(TEMPERATURE_LIMIT_C + 5, Math.ceil((temperature.high + 5) / 5) * 5),
  };
}

/** The last I/O minute, unless the disk has stopped reporting (detached). */
export function currentIo(
  entry: DiskStatsEntry | undefined,
  checkedAt: number,
  sampleSeconds: number,
) {
  const io = entry?.io;
  return io && checkedAt - io.at <= 3 * sampleSeconds ? io : null;
}

export interface HistorySeries {
  read: ChartPoint[];
  write: ChartPoint[];
  busy: ChartPoint[];
  temperature: ChartPoint[];
}

/** Plot each bucket at its midpoint as a mean rate over the minutes it recorded. */
export function historySeries(history: DiskHistory): HistorySeries {
  const middle = history.bucketSeconds / 2;
  const seconds = (minutes: number) => minutes * history.ioSampleSeconds;
  return {
    read: history.io.map((bucket) => ({
      at: bucket.start + middle,
      value: bucket.readBytes / seconds(bucket.minutes),
    })),
    write: history.io.map((bucket) => ({
      at: bucket.start + middle,
      value: bucket.writeBytes / seconds(bucket.minutes),
    })),
    busy: history.io.map((bucket) => ({ at: bucket.start + middle, value: bucket.busyPercent })),
    temperature: history.temperature.map((bucket) => ({
      at: bucket.start + middle,
      value: bucket.temperatureC,
    })),
  };
}

export interface HistorySummary {
  recordedSeconds: number;
  readBytes: number;
  writeBytes: number;
  averageRead: number;
  averageWrite: number;
  peakRead: number;
  peakWrite: number;
  averageBusy: number;
  peakBusy: number;
  temperature: { low: number; average: number; high: number } | null;
}

export function historySummary(history: DiskHistory): HistorySummary | null {
  const minutes = history.io.reduce((sum, bucket) => sum + bucket.minutes, 0);
  const temperatures = history.temperature;
  if (minutes === 0 && temperatures.length === 0) return null;
  const sum = (pick: (bucket: DiskHistory['io'][number]) => number) =>
    history.io.reduce((total, bucket) => total + pick(bucket), 0);
  const max = (pick: (bucket: DiskHistory['io'][number]) => number) =>
    history.io.reduce((top, bucket) => Math.max(top, pick(bucket)), 0);
  const recordedSeconds = minutes * history.ioSampleSeconds;
  const readBytes = sum((bucket) => bucket.readBytes);
  const writeBytes = sum((bucket) => bucket.writeBytes);
  const readings = temperatures.reduce((total, bucket) => total + bucket.readings, 0);
  return {
    recordedSeconds,
    readBytes,
    writeBytes,
    averageRead: recordedSeconds ? readBytes / recordedSeconds : 0,
    averageWrite: recordedSeconds ? writeBytes / recordedSeconds : 0,
    peakRead: max((bucket) => bucket.peakReadBytesPerSecond),
    peakWrite: max((bucket) => bucket.peakWriteBytesPerSecond),
    averageBusy: minutes ? sum((bucket) => bucket.busyPercent * bucket.minutes) / minutes : 0,
    peakBusy: max((bucket) => bucket.peakBusyPercent),
    temperature: readings
      ? {
          low: Math.min(...temperatures.map((bucket) => bucket.temperatureC)),
          average:
            temperatures.reduce(
              (total, bucket) => total + bucket.temperatureC * bucket.readings,
              0,
            ) / readings,
          high: Math.max(...temperatures.map((bucket) => bucket.maxTemperatureC)),
        }
      : null,
  };
}

/** A one-line SMART verdict for the disk card, naming any failure signal. */
export function smartVerdict(smart: SmartReading): {
  tone: 'good' | 'warning' | 'bad';
  text: string;
} {
  if (smart.passed === false) return { tone: 'bad', text: 'SMART health check failed' };
  const signals = [
    [smart.reallocatedSectors, 'reallocated'],
    [smart.pendingSectors, 'pending'],
    [smart.uncorrectableSectors, 'uncorrectable'],
  ] as const;
  const found = signals
    .filter(([count]) => (count ?? 0) > 0)
    .map(([count, name]) => `${count} ${name}`);
  if (found.length) return { tone: 'warning', text: `SMART passed · ${found.join(' · ')} sectors` };
  return { tone: 'good', text: smart.passed ? 'SMART passed' : 'SMART verdict unknown' };
}
