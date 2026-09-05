import {
  arrayValue,
  booleanValue,
  nullableBoolean,
  nullableNumber,
  nullableString,
  numberValue,
  objectValue,
  stringValue,
} from '../../api/validation';
import type {
  UbntNetwork,
  UbntOperationKind,
  UbntOperationState,
  UbntProfile,
  UbntProfileSecurity,
  UbntRateModule,
  UbntSecurity,
  UbntWifiStatus,
} from './types';

function trueValue(value: unknown, label: string): true {
  if (value !== true) throw new TypeError(`${label} must be true`);
  return true;
}

function oneOf<T extends string>(value: unknown, choices: readonly T[], label: string): T {
  const text = stringValue(value, label);
  if (!choices.includes(text as T)) {
    throw new TypeError(`${label} has an unsupported value: ${text}`);
  }
  return text as T;
}

function nullableOneOf<T extends string>(
  value: unknown,
  choices: readonly T[],
  label: string,
): T | null {
  return value === null ? null : oneOf(value, choices, label);
}

function optionalNullableNumber(value: unknown, label: string): number | null {
  return value === undefined ? null : nullableNumber(value, label);
}

function optionalNullableString(value: unknown, label: string): string | null {
  return value === undefined ? null : nullableString(value, label);
}

function optionalNullableBoolean(value: unknown, label: string): boolean | null {
  return value === undefined ? null : nullableBoolean(value, label);
}

function boundedNumber(value: unknown, label: string, minimum: number, maximum: number): number {
  const number = numberValue(value, label);
  if (number < minimum || number > maximum) {
    throw new TypeError(`${label} must be between ${minimum} and ${maximum}`);
  }
  return number;
}

function optionalNullableInteger(
  value: unknown,
  label: string,
  minimum: number,
  maximum: number,
): number | null {
  const number = optionalNullableNumber(value, label);
  if (number !== null && (!Number.isInteger(number) || number < minimum || number > maximum)) {
    throw new TypeError(`${label} must be null or an integer from ${minimum} to ${maximum}`);
  }
  return number;
}

const PROFILE_SECURITY = ['wpa', 'wep', 'none'] as const satisfies readonly UbntProfileSecurity[];
const NETWORK_SECURITY = [
  'wpa',
  'wep',
  'none',
  'enterprise',
] as const satisfies readonly UbntSecurity[];
const RATE_MODULES = ['atheros', 'ewma_ht'] as const satisfies readonly UbntRateModule[];
const OPERATION_STATES = [
  'idle',
  'running',
  'complete',
  'error',
] as const satisfies readonly UbntOperationState[];
const OPERATION_KINDS = [
  'status',
  'scan',
  'connect',
  'provision',
  'update-profile',
  'resume',
  'abort',
] as const satisfies readonly UbntOperationKind[];

function decodeProfile(value: unknown, index: number): UbntProfile {
  const label = `wifi.profiles[${index}]`;
  const profile = objectValue(value, label);
  return {
    name: stringValue(profile.name, `${label}.name`),
    ssid: stringValue(profile.ssid, `${label}.ssid`),
    security: oneOf(profile.security, PROFILE_SECURITY, `${label}.security`),
    priority: optionalNullableInteger(profile.priority, `${label}.priority`, 0, 1_000_000),
    bssid: stringValue(profile.bssid, `${label}.bssid`),
    hasPassword: nullableBoolean(profile.has_password, `${label}.has_password`),
    outputPowerDbm: optionalNullableInteger(
      profile.output_power_dbm,
      `${label}.output_power_dbm`,
      0,
      23,
    ),
    rateModule: nullableOneOf(profile.rate_module, RATE_MODULES, `${label}.rate_module`),
    rateAuto: nullableBoolean(profile.rate_auto, `${label}.rate_auto`),
    rateMcs: optionalNullableInteger(profile.rate_mcs, `${label}.rate_mcs`, 0, 15),
  };
}

function decodeNetwork(value: unknown, index: number): UbntNetwork {
  const label = `wifi.networks[${index}]`;
  const network = objectValue(value, label);
  return {
    ssid: stringValue(network.ssid, `${label}.ssid`),
    security: oneOf(network.security, NETWORK_SECURITY, `${label}.security`),
    qualityPercent: boundedNumber(network.quality_percent, `${label}.quality_percent`, 0, 100),
    frequencyMhz: optionalNullableInteger(
      network.frequency_mhz,
      `${label}.frequency_mhz`,
      0,
      100_000,
    ),
    channel: optionalNullableInteger(network.channel, `${label}.channel`, 0, 10_000),
    bssid: stringValue(network.bssid, `${label}.bssid`),
    signalDbm: optionalNullableNumber(network.signal_dbm, `${label}.signal_dbm`),
    profiles: arrayValue(network.profiles, `${label}.profiles`).map((profile, profileIndex) =>
      stringValue(profile, `${label}.profiles[${profileIndex}]`),
    ),
    known: booleanValue(network.known, `${label}.known`),
    connected: booleanValue(network.connected, `${label}.connected`),
    supported: booleanValue(network.supported, `${label}.supported`),
  };
}

export function decodeUbntWifiStatus(value: unknown): UbntWifiStatus {
  const response = objectValue(value, 'UBNT Wi-Fi response');
  trueValue(response.ok, 'UBNT Wi-Fi response.ok');
  const wifi = objectValue(response.wifi, 'wifi');
  if (wifi.version !== 1) throw new TypeError('wifi.version must be 1');
  const state = objectValue(wifi.state, 'wifi.state');
  const operation = objectValue(response.operation, 'UBNT operation');

  return {
    reachable: nullableBoolean(wifi.reachable, 'wifi.reachable'),
    checkedAt: nullableNumber(wifi.checked_at, 'wifi.checked_at'),
    state: {
      configuredSsid: nullableString(state.configured_ssid, 'wifi.state.configured_ssid'),
      associatedSsid: nullableString(state.associated_ssid, 'wifi.state.associated_ssid'),
      ccqPercent: nullableNumber(state.ccq_percent, 'wifi.state.ccq_percent'),
      automaticPaused: nullableBoolean(state.automatic_paused, 'wifi.state.automatic_paused'),
      selectorRunning: nullableBoolean(state.selector_running, 'wifi.state.selector_running'),
      signalDbm: optionalNullableNumber(state.signal_dbm, 'wifi.state.signal_dbm'),
      noiseDbm: optionalNullableNumber(state.noise_dbm, 'wifi.state.noise_dbm'),
      snrDb: optionalNullableNumber(state.snr_db, 'wifi.state.snr_db'),
    },
    profiles: arrayValue(wifi.profiles, 'wifi.profiles').map(decodeProfile),
    networks: arrayValue(wifi.networks, 'wifi.networks').map(decodeNetwork),
    operation: {
      status: oneOf(operation.status, OPERATION_STATES, 'UBNT operation.status'),
      kind: nullableOneOf(operation.kind, OPERATION_KINDS, 'UBNT operation.kind'),
      startedAt: nullableNumber(operation.started_at, 'UBNT operation.started_at'),
      completedAt: nullableNumber(operation.completed_at, 'UBNT operation.completed_at'),
      message: optionalNullableString(operation.message, 'UBNT operation.message'),
      error: nullableString(operation.error, 'UBNT operation.error'),
    },
  };
}
