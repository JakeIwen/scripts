import {
  booleanValue,
  nullableNumber,
  nullableString,
  objectValue,
  stringValue,
} from '../../api/validation';

export interface BatteryReading {
  available: boolean;
  value: number | null;
  unit: string;
  source: string | null;
  observedAt: string | null;
  detail: string;
}

export interface TelemetryServiceStatus {
  available: boolean;
  running: boolean;
  error: string | null;
}

export type VoltageCheckState = 'idle' | 'running' | 'complete' | 'error';

export interface VoltageCheckStatus {
  status: VoltageCheckState;
  startedAt: number | null;
  completedAt: number | null;
  error: string | null;
}

export interface TelemetrySummary {
  battery: BatteryReading;
  service: TelemetryServiceStatus;
  check: VoltageCheckStatus;
}

function optionalNullableString(value: unknown, label: string): string | null {
  return value === undefined ? null : nullableString(value, label);
}

function trueValue(value: unknown, label: string): true {
  if (value !== true) throw new TypeError(`${label} must be true`);
  return true;
}

const VOLTAGE_CHECK_STATES = [
  'idle',
  'running',
  'complete',
  'error',
] as const satisfies readonly VoltageCheckState[];

function voltageCheckState(value: unknown): VoltageCheckState {
  const status = stringValue(value, 'check.status');
  if (!VOLTAGE_CHECK_STATES.includes(status as VoltageCheckState)) {
    throw new TypeError(`check.status has an unsupported value: ${status}`);
  }
  return status as VoltageCheckState;
}

/** Decode /api/telemetry-summary before any values reach the tile. */
export function decodeTelemetrySummary(payload: unknown): TelemetrySummary {
  const response = objectValue(payload, 'telemetry summary response');
  trueValue(response.ok, 'telemetry summary response.ok');
  const battery = objectValue(response.battery, 'telemetry summary response.battery');
  const service = objectValue(response.service, 'telemetry summary response.service');
  const check = objectValue(response.check, 'telemetry summary response.check');

  const reading: BatteryReading = {
    available: booleanValue(battery.available, 'battery.available'),
    value: nullableNumber(battery.value, 'battery.value'),
    unit: stringValue(battery.unit, 'battery.unit'),
    source: nullableString(battery.source, 'battery.source'),
    observedAt: nullableString(battery.observed_at, 'battery.observed_at'),
    detail: stringValue(battery.detail, 'battery.detail'),
  };

  if (reading.available && reading.value === null) {
    throw new TypeError('battery.value must be present when battery.available is true');
  }

  return {
    battery: reading,
    service: {
      available: booleanValue(service.available, 'service.available'),
      running: booleanValue(service.running, 'service.running'),
      error: optionalNullableString(service.error, 'service.error'),
    },
    check: {
      status: voltageCheckState(check.status),
      startedAt: nullableNumber(check.started_at, 'check.started_at'),
      completedAt: nullableNumber(check.completed_at, 'check.completed_at'),
      error: nullableString(check.error, 'check.error'),
    },
  };
}

export function formatBatteryVoltage(battery: BatteryReading): string {
  if (!battery.available || battery.value === null) return '—';
  return `${battery.value.toFixed(2)} ${battery.unit}`;
}

export function batterySourceLabel(source: string | null): string {
  if (source === 'live') return 'Live telemetry';
  if (source === 'voltage_mon') return 'Last voltage monitor reading';
  return source ?? 'Source unavailable';
}

export function voltageCheckLabel(check: VoltageCheckStatus): string {
  switch (check.status) {
    case 'idle':
      return 'Idle';
    case 'running':
      return 'Running now';
    case 'complete':
      return 'Complete';
    case 'error':
      return check.error ? `Failed · ${check.error}` : 'Failed';
  }
}
