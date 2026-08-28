export type MwanInterfaceState =
  'online' | 'offline' | 'disabled' | 'unknown' | 'connecting' | 'disconnecting';

export interface MwanInterface {
  name: string;
  state: MwanInterfaceState;
  tracking: 'active' | 'paused';
  detail: string | null;
}

export interface MwanRouteMember {
  name: string;
  percent: number;
}

export interface RouterConnectivity {
  reachable: boolean | null;
  mode: string | null;
  online: string[];
  interfaces: MwanInterface[];
  defaultPolicy: string | null;
  routeMembers: MwanRouteMember[];
  error: string | null;
}

export interface UbntLinkStatus {
  reachable: boolean | null;
  connected: boolean | null;
  ssid: string | null;
  accessPoint: string | null;
  signalDbm: number | null;
  noiseDbm: number | null;
  qualityPercent: number | null;
  ccqPercent: number | null;
  bitrate: string | null;
  frequencyGhz: number | null;
  error: string | null;
}

export interface ConnectivityStatus {
  checkedAt: number | null;
  internetOnline: boolean | null;
  internetSource: string;
  router: RouterConnectivity;
  ubnt: UbntLinkStatus;
  refreshing: boolean;
  lastError: string | null;
  activeMode: boolean;
  stale: boolean;
}

export type OpenWrtConnectionKind = 'wifi' | 'lan';
export type OpenWrtBand = '2.4 GHz' | '5 GHz' | '6 GHz';
export type OpenWrtNeighborState = 'REACHABLE' | 'DELAY' | 'PROBE' | 'PERMANENT' | 'STALE';

export interface OpenWrtClient {
  name: string;
  hostnameKnown: boolean;
  ip: string | null;
  mac: string;
  connection: OpenWrtConnectionKind;
  interfaceName: string;
  radio: string | null;
  band: OpenWrtBand | null;
  neighborState: OpenWrtNeighborState | null;
  signalDbm: number | null;
  receiveRateBps: number | null;
  transmitRateBps: number | null;
  receivedBytes: number | null;
  transmittedBytes: number | null;
  leaseExpiresAt: number | null;
}

export interface OpenWrtClientStatus {
  checkedAt: number;
  clientCount: number;
  wifiCount: number;
  lanCount: number;
  clients: OpenWrtClient[];
}

export type SpeedtestState = 'idle' | 'running' | 'complete' | 'error';

export interface SpeedtestStatus {
  status: SpeedtestState;
  startedAt: number | null;
  completedAt: number | null;
  downloadMbps: number | null;
  uploadMbps: number | null;
  latencyMs: number | null;
  error: string | null;
}
