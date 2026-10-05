import * as v from 'valibot';

import {
  booleanValue,
  decode,
  finiteNumber,
  nonnegativeInteger,
  nullableBoolean,
  nullableNumber,
  nullableOneOf,
  nullableString,
  object,
  objectInput,
  oneOf,
  optionalNullableNumber,
  optionalNullableString,
  stringArray,
  text,
  trueValue,
} from '../../api/schema';

const deferred = v.optional(v.unknown());
const versionOne = v.pipe(
  v.unknown(),
  v.check((value) => value === 1, 'must be 1'),
);

export const mwanInterfaceSchema = object({
  name: text,
  state: oneOf([
    'online',
    'degraded',
    'offline',
    'disabled',
    'unknown',
    'connecting',
    'disconnecting',
  ]),
  tracking: oneOf(['active', 'paused']),
  detail: nullableString,
});
export const mwanRouteMemberSchema = object({ name: text, percent: finiteNumber });

const routerSchema = v.pipe(
  object({
    reachable: nullableBoolean,
    mode: nullableString,
    online: stringArray,
    interfaces: v.array(mwanInterfaceSchema),
    default_policy: nullableString,
    route_members: v.array(mwanRouteMemberSchema),
    error: nullableString,
  }),
  v.transform(({ default_policy, route_members, ...rest }) => ({
    ...rest,
    defaultPolicy: default_policy,
    routeMembers: route_members,
  })),
);
const internetSchema = object({ online: nullableBoolean, source: text });

export const ubntLinkSchema = v.pipe(
  object({
    reachable: nullableBoolean,
    connected: nullableBoolean,
    ssid: nullableString,
    access_point: optionalNullableString,
    signal_dbm: nullableNumber,
    noise_dbm: nullableNumber,
    quality_percent: nullableNumber,
    ccq_percent: nullableNumber,
    bitrate: nullableString,
    frequency_ghz: optionalNullableNumber,
    error: nullableString,
  }),
  v.transform((row) => ({
    reachable: row.reachable,
    connected: row.connected,
    ssid: row.ssid,
    accessPoint: row.access_point,
    signalDbm: row.signal_dbm,
    noiseDbm: row.noise_dbm,
    qualityPercent: row.quality_percent,
    ccqPercent: row.ccq_percent,
    bitrate: row.bitrate,
    frequencyGhz: row.frequency_ghz,
    error: row.error,
  })),
);

const connectivitySchema = v.pipe(
  // Both sibling objects must exist before checked_at or any nested field is checked.
  object({
    internet: objectInput,
    router: objectInput,
    checked_at: deferred,
    ubnt: deferred,
    refreshing: deferred,
    last_error: deferred,
    active_mode: deferred,
    stale: deferred,
  }),
  // Only container shapes are known yet; the next schema validates their full contents.
  v.transform((input): unknown => input),
  object({
    checked_at: nullableNumber,
    internet: internetSchema,
    router: routerSchema,
    ubnt: ubntLinkSchema,
    refreshing: booleanValue,
    last_error: nullableString,
    active_mode: booleanValue,
    stale: booleanValue,
  }),
  v.transform((row) => ({
    checkedAt: row.checked_at,
    internetOnline: row.internet.online,
    internetSource: row.internet.source,
    router: row.router,
    ubnt: row.ubnt,
    refreshing: row.refreshing,
    lastError: row.last_error,
    activeMode: row.active_mode,
    stale: row.stale,
  })),
);

export const connectivityResponseSchema = v.pipe(
  object({ ok: trueValue, connectivity: deferred }),
  // The envelope's ok check must precede the independently labelled connectivity preflights.
  v.transform((row) => decode(connectivitySchema, row.connectivity, 'connectivity')),
);

export const openWrtClientSchema = v.pipe(
  object({
    name: text,
    hostname_known: booleanValue,
    ip: nullableString,
    mac: text,
    connection: oneOf(['wifi', 'lan']),
    interface: text,
    radio: nullableString,
    band: nullableOneOf(['2.4 GHz', '5 GHz', '6 GHz']),
    neighbor_state: nullableOneOf(['REACHABLE', 'DELAY', 'PROBE', 'PERMANENT', 'STALE']),
    signal_dbm: nullableNumber,
    rx_rate_bps: nullableNumber,
    tx_rate_bps: nullableNumber,
    rx_bytes: nullableNumber,
    tx_bytes: nullableNumber,
    lease_expires_at: nullableNumber,
  }),
  v.transform((row) => ({
    name: row.name,
    hostnameKnown: row.hostname_known,
    ip: row.ip,
    mac: row.mac,
    connection: row.connection,
    interfaceName: row.interface,
    radio: row.radio,
    band: row.band,
    neighborState: row.neighbor_state,
    signalDbm: row.signal_dbm,
    receiveRateBps: row.rx_rate_bps,
    transmitRateBps: row.tx_rate_bps,
    receivedBytes: row.rx_bytes,
    transmittedBytes: row.tx_bytes,
    leaseExpiresAt: row.lease_expires_at,
  })),
);

const openWrtSchema = v.pipe(
  object({
    version: versionOne,
    clients: v.array(openWrtClientSchema),
    client_count: nonnegativeInteger,
    wifi_count: nonnegativeInteger,
    lan_count: nonnegativeInteger,
    checked_at: deferred,
  }),
  v.check((row) => {
    const wifi = row.clients.filter((client) => client.connection === 'wifi').length;
    return (
      row.client_count === row.clients.length &&
      row.wifi_count === wifi &&
      row.lan_count === row.clients.length - wifi
    );
  }, 'client counts do not match openwrt.clients'),
  v.transform((row) => ({
    // Count consistency must fail before checked_at is validated, including when it is absent.
    checkedAt: decode(nonnegativeInteger, row.checked_at, 'openwrt.checked_at'),
    clientCount: row.client_count,
    wifiCount: row.wifi_count,
    lanCount: row.lan_count,
    clients: row.clients,
  })),
);

export const openWrtClientsResponseSchema = v.pipe(
  object({ ok: trueValue, openwrt: deferred }),
  // The envelope's ok check precedes OpenWrt version, clients and count validation.
  v.transform((row) => decode(openWrtSchema, row.openwrt, 'openwrt')),
);

const speedtestSchema = v.pipe(
  object({
    status: oneOf(['idle', 'running', 'complete', 'error']),
    started_at: nullableNumber,
    completed_at: nullableNumber,
    download_mbps: nullableNumber,
    upload_mbps: nullableNumber,
    latency_ms: nullableNumber,
    error: nullableString,
  }),
  v.transform((row) => ({
    status: row.status,
    startedAt: row.started_at,
    completedAt: row.completed_at,
    downloadMbps: row.download_mbps,
    uploadMbps: row.upload_mbps,
    latencyMs: row.latency_ms,
    error: row.error,
  })),
);

export const speedtestResponseSchema = v.pipe(
  object({ ok: trueValue, speedtest: deferred }),
  // The envelope's ok check precedes the independently labelled speedtest fields.
  v.transform((row) => decode(speedtestSchema, row.speedtest, 'speedtest')),
);
export const speedtestStartResponseSchema = v.pipe(
  object({ message: text, ok: deferred, speedtest: deferred }),
  v.transform((row) => ({
    message: row.message,
    // A start response validates its message before delegating ok and status checks.
    status: decodeSpeedtestResponse(row),
  })),
);

export function decodeConnectivityResponse(value: unknown): ConnectivityStatus {
  return decode(connectivityResponseSchema, value, 'connectivity response');
}
export function decodeOpenWrtClientsResponse(value: unknown): OpenWrtClientStatus {
  return decode(openWrtClientsResponseSchema, value, 'OpenWrt clients response');
}
export function decodeSpeedtestResponse(value: unknown): SpeedtestStatus {
  return decode(speedtestResponseSchema, value, 'speed test response');
}
export function decodeStartSpeedtestResponse(value: unknown): SpeedtestStartResult {
  return decode(speedtestStartResponseSchema, value, 'speed test start response');
}

export type MwanInterface = v.InferOutput<typeof mwanInterfaceSchema>;
export type MwanInterfaceState = MwanInterface['state'];
export type MwanRouteMember = v.InferOutput<typeof mwanRouteMemberSchema>;
export type RouterConnectivity = v.InferOutput<typeof routerSchema>;
export type UbntLinkStatus = v.InferOutput<typeof ubntLinkSchema>;
export type ConnectivityStatus = v.InferOutput<typeof connectivityResponseSchema>;
export type OpenWrtClient = v.InferOutput<typeof openWrtClientSchema>;
export type OpenWrtConnectionKind = OpenWrtClient['connection'];
export type OpenWrtBand = NonNullable<OpenWrtClient['band']>;
export type OpenWrtNeighborState = NonNullable<OpenWrtClient['neighborState']>;
export type OpenWrtClientStatus = v.InferOutput<typeof openWrtClientsResponseSchema>;
export type SpeedtestStatus = v.InferOutput<typeof speedtestSchema>;
export type SpeedtestState = SpeedtestStatus['status'];
export type SpeedtestStartResult = v.InferOutput<typeof speedtestStartResponseSchema>;
