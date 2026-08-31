import type {
  CurrentSystemSample,
  ProjectedMetric,
  RepeatProcessOffender,
  SystemHealthEvent,
  SystemHealthEvidence,
  SystemEventSeverity,
} from './types';

type UnknownRecord = Record<string, unknown>;

/** Producer-owned nested objects are optional and deliberately projected here. */
function record(value: unknown): UnknownRecord | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function nonnegativeInteger(value: unknown): number {
  const number = finiteNumber(value);
  return number !== null && Number.isInteger(number) && number >= 0 ? number : 0;
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function textList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

function primaryTemperature(current: UnknownRecord): number | null {
  if (Array.isArray(current.thermal_sensors)) {
    const sensors = current.thermal_sensors
      .map(record)
      .filter((sensor): sensor is UnknownRecord => sensor !== null);
    const primary =
      sensors.find((sensor) => text(sensor.type)?.toLowerCase().includes('cpu')) ?? sensors[0];
    const temperature = finiteNumber(primary?.temperature_c);
    if (temperature !== null) return temperature;
  }
  return finiteNumber(current.temperature_c);
}

export function projectCurrentSystemSample(value: unknown): CurrentSystemSample | null {
  const current = record(value);
  if (!current) return null;

  const memory = record(current.memory);
  const throttle = record(current.throttle);
  const network = record(current.network_io);
  const disk = record(current.disk_io);

  return {
    observedAt: finiteNumber(current.timestamp),
    cpuPercent: finiteNumber(current.cpu_percent),
    memoryPercent: finiteNumber(memory?.used_percent),
    temperatureCelsius: primaryTemperature(current),
    armMegahertz: finiteNumber(current.arm_mhz),
    throttleWord: text(throttle?.hex),
    activeThrottleFlags: textList(throttle?.current),
    occurredThrottleFlags: textList(throttle?.occurred),
    networkReceiveBytesPerSecond: finiteNumber(network?.rx_bytes_per_second),
    networkTransmitBytesPerSecond: finiteNumber(network?.tx_bytes_per_second),
    diskReadBytesPerSecond: finiteNumber(disk?.read_bytes_per_second),
    diskWriteBytesPerSecond: finiteNumber(disk?.write_bytes_per_second),
    diskBusyPercent: finiteNumber(disk?.busy_percent),
  };
}

export function projectPeakMetric(container: unknown, key: string): ProjectedMetric {
  const metric = record(record(container)?.[key]);
  return {
    value: finiteNumber(metric?.value),
    observedAt: finiteNumber(metric?.at),
  };
}

export function projectSystemHealthEvidence(value: unknown): SystemHealthEvidence {
  const evidence = record(value);
  return {
    undervoltageEpisodes: nonnegativeInteger(evidence?.undervoltage_episodes),
    undervoltageSeconds: finiteNumber(evidence?.undervoltage_seconds) ?? 0,
    usbFailures: nonnegativeInteger(evidence?.usb_failures),
    storageErrors: nonnegativeInteger(evidence?.storage_errors),
  };
}

export function projectRepeatOffenders(value: unknown): RepeatProcessOffender[] {
  const rows = Array.isArray(record(value)?.repeat_offenders)
    ? (record(value)?.repeat_offenders as unknown[])
    : [];
  return rows.flatMap((row) => {
    const process = record(row);
    const name = text(process?.name);
    if (!process || !name) return [];
    return [
      {
        name,
        peakCount: nonnegativeInteger(process.peak_count),
        cpuPeakCount: nonnegativeInteger(process.cpu_peak_count),
        memoryPeakCount: nonnegativeInteger(process.memory_peak_count),
        maximumCpuPercent: finiteNumber(process.max_cpu_percent),
        maximumResidentBytes: finiteNumber(process.max_rss_bytes),
        lastSeenAt: finiteNumber(process.last_seen_at),
      },
    ];
  });
}

function eventSeverity(value: unknown): SystemEventSeverity {
  return value === 'info' || value === 'warning' || value === 'critical' ? value : 'unknown';
}

export function projectSystemHealthEvents(value: unknown): SystemHealthEvent[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((row) => {
    const event = record(row);
    const timestamp = finiteNumber(event?.timestamp);
    const summary = text(event?.summary);
    if (!event || timestamp === null || !summary) return [];
    return [
      {
        timestamp,
        severity: eventSeverity(event.severity),
        category: text(event.category) ?? 'system',
        summary,
        message: text(event.message) ?? '',
      },
    ];
  });
}
