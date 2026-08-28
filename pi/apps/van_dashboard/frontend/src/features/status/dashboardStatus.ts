import {
  booleanValue,
  nullableNumber,
  nullableString,
  objectValue,
  stringValue,
} from '../../api/validation';

export interface CopAlertRequest {
  requested: boolean;
  ignitionOn: boolean;
  exteriorFloodState: string | null;
  lastNotificationAt: number | null;
  lastError: string | null;
}

export type CopCanWakeState =
  | 'idle'
  | 'starting'
  | 'arming_delay'
  | 'paused_ignition'
  | 'blocked'
  | 'waking'
  | 'active_waiting'
  | 'stopping'
  | 'stopped';

export interface CopAlertExecution {
  available: boolean;
  serviceActive: boolean | null;
  state: CopCanWakeState | null;
  markerActive: boolean | null;
  lastDetail: string | null;
  lastBlockedReason: string | null;
  lastBlockedDetail: string | null;
  error: string | null;
}

export interface CopLedStatus {
  phase: string;
  message: string;
  lastError: string | null;
}

export interface SystemUptime {
  seconds: number | null;
  bootedAt: string | null;
}

export interface StarlinkStatus {
  state: 'on' | 'off' | 'unknown';
  available: boolean;
  changing: boolean;
  lastError: string | null;
}

export interface DashboardStatus {
  copAlert: CopAlertRequest;
  copExecution: CopAlertExecution;
  copLed: CopLedStatus;
  starlink: StarlinkStatus;
  systemUptime: SystemUptime;
}

function optionalNullableString(value: unknown, label: string): string | null {
  return value === undefined ? null : nullableString(value, label);
}

function optionalNullableNumber(value: unknown, label: string): number | null {
  return value === undefined ? null : nullableNumber(value, label);
}

function optionalNullableBoolean(value: unknown, label: string): boolean | null {
  if (value === undefined || value === null) return null;
  return booleanValue(value, label);
}

function trueValue(value: unknown, label: string): true {
  if (value !== true) throw new TypeError(`${label} must be true`);
  return true;
}

const COP_CAN_WAKE_STATES = [
  'idle',
  'starting',
  'arming_delay',
  'paused_ignition',
  'blocked',
  'waking',
  'active_waiting',
  'stopping',
  'stopped',
] as const satisfies readonly CopCanWakeState[];

function optionalCopCanWakeState(value: unknown): CopCanWakeState | null {
  if (value === undefined || value === null) return null;
  const state = stringValue(value, 'cop_can_wake.state');
  if (!COP_CAN_WAKE_STATES.includes(state as CopCanWakeState)) {
    throw new TypeError(`cop_can_wake.state has an unsupported value: ${state}`);
  }
  return state as CopCanWakeState;
}

/** Decode the small, stable part of /api/status used by the preview. */
export function decodeDashboardStatus(payload: unknown): DashboardStatus {
  const response = objectValue(payload, 'dashboard status response');
  trueValue(response.ok, 'dashboard status response.ok');
  const copAlert = objectValue(response.cop_alert, 'dashboard status response.cop_alert');
  const copExecution = objectValue(response.cop_can_wake, 'dashboard status response.cop_can_wake');
  const copLed = objectValue(response.cop_led, 'dashboard status response.cop_led');
  const starlink = objectValue(response.starlink, 'dashboard status response.starlink');
  const uptime = objectValue(response.system_uptime, 'dashboard status response.system_uptime');

  return {
    copAlert: decodeCopAlertRequest(copAlert),
    copExecution: {
      available: booleanValue(copExecution.available, 'cop_can_wake.available'),
      serviceActive: optionalNullableBoolean(
        copExecution.service_active,
        'cop_can_wake.service_active',
      ),
      state: optionalCopCanWakeState(copExecution.state),
      markerActive: optionalNullableBoolean(
        copExecution.marker_active,
        'cop_can_wake.marker_active',
      ),
      lastDetail: optionalNullableString(copExecution.last_detail, 'cop_can_wake.last_detail'),
      lastBlockedReason: optionalNullableString(
        copExecution.last_blocked_reason,
        'cop_can_wake.last_blocked_reason',
      ),
      lastBlockedDetail: optionalNullableString(
        copExecution.last_blocked_detail,
        'cop_can_wake.last_blocked_detail',
      ),
      error: optionalNullableString(copExecution.error, 'cop_can_wake.error'),
    },
    copLed: {
      phase: stringValue(copLed.phase, 'cop_led.phase'),
      message: stringValue(copLed.message, 'cop_led.message'),
      lastError: optionalNullableString(copLed.last_error, 'cop_led.last_error'),
    },
    starlink: decodeStarlinkStatus(starlink),
    systemUptime: {
      seconds: nullableNumber(uptime.seconds, 'system_uptime.seconds'),
      bootedAt: nullableString(uptime.booted_at, 'system_uptime.booted_at'),
    },
  };
}

export function decodeCopAlertRequest(value: unknown): CopAlertRequest {
  const copAlert = objectValue(value, 'cop_alert');
  return {
    requested: booleanValue(copAlert.active, 'cop_alert.active'),
    ignitionOn: booleanValue(copAlert.ignition_on, 'cop_alert.ignition_on'),
    exteriorFloodState: optionalNullableString(copAlert.ext_flood, 'cop_alert.ext_flood'),
    lastNotificationAt: optionalNullableNumber(copAlert.last_ntfy, 'cop_alert.last_ntfy'),
    lastError: optionalNullableString(copAlert.last_error, 'cop_alert.last_error'),
  };
}

export function decodeStarlinkStatus(value: unknown): StarlinkStatus {
  const starlink = objectValue(value, 'starlink');
  const state = stringValue(starlink.state, 'starlink.state');
  if (!['on', 'off', 'unknown'].includes(state)) {
    throw new TypeError(`starlink.state has an unsupported value: ${state}`);
  }
  return {
    state: state as StarlinkStatus['state'],
    available: booleanValue(starlink.available, 'starlink.available'),
    changing: booleanValue(starlink.changing, 'starlink.changing'),
    lastError: optionalNullableString(starlink.last_error, 'starlink.last_error'),
  };
}

export function formatDashboardUptime(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) {
    return 'Uptime unavailable';
  }

  const wholeSeconds = Math.floor(seconds);
  const days = Math.floor(wholeSeconds / 86_400);
  const hours = Math.floor((wholeSeconds % 86_400) / 3_600);
  const minutes = Math.floor((wholeSeconds % 3_600) / 60);
  const parts: string[] = [];

  if (days > 0) parts.push(`${days}d`);
  if (days > 0 || hours > 0) parts.push(`${hours}h`);
  parts.push(`${minutes}m`);
  return `Uptime · ${parts.join(' ')}`;
}
