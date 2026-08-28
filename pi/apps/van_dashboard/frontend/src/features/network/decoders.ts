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
  ConnectivityStatus,
  MwanInterface,
  MwanInterfaceState,
  MwanRouteMember,
  OpenWrtBand,
  OpenWrtClient,
  OpenWrtClientStatus,
  OpenWrtConnectionKind,
  OpenWrtNeighborState,
  SpeedtestState,
  SpeedtestStatus,
  UbntLinkStatus,
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

function optionalNullableString(value: unknown, label: string): string | null {
  return value === undefined ? null : nullableString(value, label);
}

function optionalNullableNumber(value: unknown, label: string): number | null {
  return value === undefined ? null : nullableNumber(value, label);
}

function nonnegativeInteger(value: unknown, label: string): number {
  const number = numberValue(value, label);
  if (!Number.isInteger(number) || number < 0) {
    throw new TypeError(`${label} must be a non-negative integer`);
  }
  return number;
}

function stringArray(value: unknown, label: string): string[] {
  return arrayValue(value, label).map((item, index) => stringValue(item, `${label}[${index}]`));
}

const MWAN_INTERFACE_STATES = [
  'online',
  'offline',
  'disabled',
  'unknown',
  'connecting',
  'disconnecting',
] as const satisfies readonly MwanInterfaceState[];

function decodeMwanInterface(value: unknown, index: number): MwanInterface {
  const label = `connectivity.router.interfaces[${index}]`;
  const object = objectValue(value, label);
  return {
    name: stringValue(object.name, `${label}.name`),
    state: oneOf(object.state, MWAN_INTERFACE_STATES, `${label}.state`),
    tracking: oneOf(object.tracking, ['active', 'paused'], `${label}.tracking`),
    detail: nullableString(object.detail, `${label}.detail`),
  };
}

function decodeRouteMember(value: unknown, index: number): MwanRouteMember {
  const label = `connectivity.router.route_members[${index}]`;
  const object = objectValue(value, label);
  return {
    name: stringValue(object.name, `${label}.name`),
    percent: numberValue(object.percent, `${label}.percent`),
  };
}

function decodeUbntLink(value: unknown): UbntLinkStatus {
  const object = objectValue(value, 'connectivity.ubnt');
  return {
    reachable: nullableBoolean(object.reachable, 'connectivity.ubnt.reachable'),
    connected: nullableBoolean(object.connected, 'connectivity.ubnt.connected'),
    ssid: nullableString(object.ssid, 'connectivity.ubnt.ssid'),
    accessPoint: optionalNullableString(object.access_point, 'connectivity.ubnt.access_point'),
    signalDbm: nullableNumber(object.signal_dbm, 'connectivity.ubnt.signal_dbm'),
    noiseDbm: nullableNumber(object.noise_dbm, 'connectivity.ubnt.noise_dbm'),
    qualityPercent: nullableNumber(object.quality_percent, 'connectivity.ubnt.quality_percent'),
    ccqPercent: nullableNumber(object.ccq_percent, 'connectivity.ubnt.ccq_percent'),
    bitrate: nullableString(object.bitrate, 'connectivity.ubnt.bitrate'),
    frequencyGhz: optionalNullableNumber(object.frequency_ghz, 'connectivity.ubnt.frequency_ghz'),
    error: nullableString(object.error, 'connectivity.ubnt.error'),
  };
}

export function decodeConnectivityResponse(value: unknown): ConnectivityStatus {
  const response = objectValue(value, 'connectivity response');
  trueValue(response.ok, 'connectivity response.ok');
  const connectivity = objectValue(response.connectivity, 'connectivity');
  const internet = objectValue(connectivity.internet, 'connectivity.internet');
  const router = objectValue(connectivity.router, 'connectivity.router');

  return {
    checkedAt: nullableNumber(connectivity.checked_at, 'connectivity.checked_at'),
    internetOnline: nullableBoolean(internet.online, 'connectivity.internet.online'),
    internetSource: stringValue(internet.source, 'connectivity.internet.source'),
    router: {
      reachable: nullableBoolean(router.reachable, 'connectivity.router.reachable'),
      mode: nullableString(router.mode, 'connectivity.router.mode'),
      online: stringArray(router.online, 'connectivity.router.online'),
      interfaces: arrayValue(router.interfaces, 'connectivity.router.interfaces').map(
        decodeMwanInterface,
      ),
      defaultPolicy: nullableString(router.default_policy, 'connectivity.router.default_policy'),
      routeMembers: arrayValue(router.route_members, 'connectivity.router.route_members').map(
        decodeRouteMember,
      ),
      error: nullableString(router.error, 'connectivity.router.error'),
    },
    ubnt: decodeUbntLink(connectivity.ubnt),
    refreshing: booleanValue(connectivity.refreshing, 'connectivity.refreshing'),
    lastError: nullableString(connectivity.last_error, 'connectivity.last_error'),
    activeMode: booleanValue(connectivity.active_mode, 'connectivity.active_mode'),
    stale: booleanValue(connectivity.stale, 'connectivity.stale'),
  };
}

const CONNECTION_KINDS = ['wifi', 'lan'] as const satisfies readonly OpenWrtConnectionKind[];
const BANDS = ['2.4 GHz', '5 GHz', '6 GHz'] as const satisfies readonly OpenWrtBand[];
const NEIGHBOR_STATES = [
  'REACHABLE',
  'DELAY',
  'PROBE',
  'PERMANENT',
  'STALE',
] as const satisfies readonly OpenWrtNeighborState[];

function decodeOpenWrtClient(value: unknown, index: number): OpenWrtClient {
  const label = `openwrt.clients[${index}]`;
  const object = objectValue(value, label);
  return {
    name: stringValue(object.name, `${label}.name`),
    hostnameKnown: booleanValue(object.hostname_known, `${label}.hostname_known`),
    ip: nullableString(object.ip, `${label}.ip`),
    mac: stringValue(object.mac, `${label}.mac`),
    connection: oneOf(object.connection, CONNECTION_KINDS, `${label}.connection`),
    interfaceName: stringValue(object.interface, `${label}.interface`),
    radio: nullableString(object.radio, `${label}.radio`),
    band: nullableOneOf(object.band, BANDS, `${label}.band`),
    neighborState: nullableOneOf(object.neighbor_state, NEIGHBOR_STATES, `${label}.neighbor_state`),
    signalDbm: nullableNumber(object.signal_dbm, `${label}.signal_dbm`),
    receiveRateBps: nullableNumber(object.rx_rate_bps, `${label}.rx_rate_bps`),
    transmitRateBps: nullableNumber(object.tx_rate_bps, `${label}.tx_rate_bps`),
    receivedBytes: nullableNumber(object.rx_bytes, `${label}.rx_bytes`),
    transmittedBytes: nullableNumber(object.tx_bytes, `${label}.tx_bytes`),
    leaseExpiresAt: nullableNumber(object.lease_expires_at, `${label}.lease_expires_at`),
  };
}

export function decodeOpenWrtClientsResponse(value: unknown): OpenWrtClientStatus {
  const response = objectValue(value, 'OpenWrt clients response');
  trueValue(response.ok, 'OpenWrt clients response.ok');
  const openwrt = objectValue(response.openwrt, 'openwrt');
  if (openwrt.version !== 1) throw new TypeError('openwrt.version must be 1');

  const clients = arrayValue(openwrt.clients, 'openwrt.clients').map(decodeOpenWrtClient);
  const clientCount = nonnegativeInteger(openwrt.client_count, 'openwrt.client_count');
  const wifiCount = nonnegativeInteger(openwrt.wifi_count, 'openwrt.wifi_count');
  const lanCount = nonnegativeInteger(openwrt.lan_count, 'openwrt.lan_count');
  const observedWifi = clients.filter((client) => client.connection === 'wifi').length;

  if (
    clientCount !== clients.length ||
    wifiCount !== observedWifi ||
    lanCount !== clients.length - observedWifi
  ) {
    throw new TypeError('openwrt client counts do not match openwrt.clients');
  }

  return {
    checkedAt: nonnegativeInteger(openwrt.checked_at, 'openwrt.checked_at'),
    clientCount,
    wifiCount,
    lanCount,
    clients,
  };
}

const SPEEDTEST_STATES = [
  'idle',
  'running',
  'complete',
  'error',
] as const satisfies readonly SpeedtestState[];

export function decodeSpeedtestResponse(value: unknown): SpeedtestStatus {
  const response = objectValue(value, 'speed test response');
  trueValue(response.ok, 'speed test response.ok');
  const speedtest = objectValue(response.speedtest, 'speedtest');
  return {
    status: oneOf(speedtest.status, SPEEDTEST_STATES, 'speedtest.status'),
    startedAt: nullableNumber(speedtest.started_at, 'speedtest.started_at'),
    completedAt: nullableNumber(speedtest.completed_at, 'speedtest.completed_at'),
    downloadMbps: nullableNumber(speedtest.download_mbps, 'speedtest.download_mbps'),
    uploadMbps: nullableNumber(speedtest.upload_mbps, 'speedtest.upload_mbps'),
    latencyMs: nullableNumber(speedtest.latency_ms, 'speedtest.latency_ms'),
    error: nullableString(speedtest.error, 'speedtest.error'),
  };
}
