import {
  arrayValue,
  booleanValue,
  nullableNumber,
  numberValue,
  objectValue,
  stringValue,
} from '../../api/validation';
import {
  projectCurrentSystemSample,
  projectPeakMetric,
  projectRepeatOffenders,
  projectSystemHealthEvents,
  projectSystemHealthEvidence,
} from './projections';
import {
  SYSTEM_HEALTH_RANGES,
  type SystemHealthLevel,
  type SystemHealthRange,
  type SystemHealthReport,
} from './types';

function trueValue(value: unknown, label: string): true {
  if (value !== true) throw new TypeError(`${label} must be true`);
  return true;
}

function optionalNullableNumber(value: unknown, label: string): number | null {
  return value === undefined ? null : nullableNumber(value, label);
}

function optionalStringList(value: unknown, label: string): string[] {
  if (value === undefined) return [];
  return arrayValue(value, label).map((item, index) => stringValue(item, `${label}[${index}]`));
}

function healthLevel(value: unknown): SystemHealthLevel {
  const level = stringValue(value, 'diagnosis.level');
  if (!['good', 'warning', 'critical', 'unknown'].includes(level)) {
    throw new TypeError(`diagnosis.level has an unsupported value: ${level}`);
  }
  return level as SystemHealthLevel;
}

function reportRange(value: unknown, requestedRange: SystemHealthRange): SystemHealthRange {
  if (value === undefined) return requestedRange;
  const range = objectValue(value, 'system health range');
  const hours = numberValue(range.hours, 'system health range.hours');
  if (!SYSTEM_HEALTH_RANGES.includes(hours as SystemHealthRange)) {
    throw new TypeError(`system health range.hours has an unsupported value: ${hours}`);
  }
  return hours as SystemHealthRange;
}

export function decodeSystemHealthReport(
  value: unknown,
  requestedRange: SystemHealthRange,
): SystemHealthReport {
  const response = objectValue(value, 'system health response');
  trueValue(response.ok, 'system health response.ok');
  const status = objectValue(response.status, 'system health status');
  const diagnosis = objectValue(response.diagnosis, 'system health diagnosis');
  const summary =
    response.summary === undefined ? null : objectValue(response.summary, 'system health summary');
  const events = projectSystemHealthEvents(response.events);

  return {
    generatedAt: optionalNullableNumber(response.generated_at, 'system health generated_at'),
    rangeHours: reportRange(response.range, requestedRange),
    available: booleanValue(status.available, 'system health status.available'),
    stale: booleanValue(status.stale, 'system health status.stale'),
    sampleAgeSeconds: optionalNullableNumber(
      status.sample_age_seconds,
      'system health status.sample_age_seconds',
    ),
    level: healthLevel(diagnosis.level),
    headline: stringValue(diagnosis.headline, 'diagnosis.headline'),
    findings: optionalStringList(diagnosis.findings, 'diagnosis.findings'),
    nextSteps: optionalStringList(diagnosis.next_steps, 'diagnosis.next_steps'),
    evidence: projectSystemHealthEvidence(diagnosis.evidence),
    eventCount: summary
      ? numberValue(summary.events, 'system health summary.events')
      : events.length,
    current: projectCurrentSystemSample(status.current),
    peaks: {
      cpuPercent: projectPeakMetric(response.peaks, 'cpu_percent'),
      memoryPercent: projectPeakMetric(response.peaks, 'memory_percent'),
      temperatureCelsius: projectPeakMetric(response.peaks, 'temperature_c'),
      networkReceiveBytesPerSecond: projectPeakMetric(
        response.peaks,
        'network_rx_bytes_per_second',
      ),
      networkTransmitBytesPerSecond: projectPeakMetric(
        response.peaks,
        'network_tx_bytes_per_second',
      ),
      diskReadBytesPerSecond: projectPeakMetric(response.peaks, 'disk_read_bytes_per_second'),
      diskWriteBytesPerSecond: projectPeakMetric(response.peaks, 'disk_write_bytes_per_second'),
      diskBusyPercent: projectPeakMetric(response.peaks, 'disk_busy_percent'),
    },
    repeatOffenders: projectRepeatOffenders(response.processes),
    events,
  };
}
