export type UbntSecurity = 'wpa' | 'wep' | 'none' | 'enterprise';
export type UbntProfileSecurity = Exclude<UbntSecurity, 'enterprise'>;
export type UbntRateModule = 'atheros' | 'ewma_ht';
export type UbntOperationState = 'idle' | 'running' | 'complete' | 'error';
export type UbntOperationKind =
  'status' | 'scan' | 'connect' | 'provision' | 'update-profile' | 'resume';

export interface UbntRadioState {
  configuredSsid: string | null;
  associatedSsid: string | null;
  ccqPercent: number | null;
  automaticPaused: boolean | null;
  selectorRunning: boolean | null;
  signalDbm: number | null;
  noiseDbm: number | null;
  snrDb: number | null;
}

export interface UbntProfile {
  name: string;
  ssid: string;
  security: UbntProfileSecurity;
  priority: number | null;
  bssid: string;
  hasPassword: boolean | null;
  outputPowerDbm: number | null;
  rateModule: UbntRateModule | null;
  rateAuto: boolean | null;
  rateMcs: number | null;
}

export interface UbntNetwork {
  ssid: string;
  security: UbntSecurity;
  qualityPercent: number;
  frequencyMhz: number | null;
  channel: number | null;
  bssid: string;
  signalDbm: number | null;
  profiles: string[];
  known: boolean;
  connected: boolean;
  supported: boolean;
}

export interface UbntOperation {
  status: UbntOperationState;
  kind: UbntOperationKind | null;
  startedAt: number | null;
  completedAt: number | null;
  message: string | null;
  error: string | null;
}

export interface UbntWifiStatus {
  reachable: boolean | null;
  checkedAt: number | null;
  state: UbntRadioState;
  profiles: UbntProfile[];
  networks: UbntNetwork[];
  operation: UbntOperation;
}

export interface UbntProvisionRequest {
  ssid: string;
  security: 'wpa' | 'none';
  bssid: string;
  password: string;
}

export interface UbntProfileUpdate {
  profile: string;
  password: string;
  bssid: string;
  outputPowerDbm: number;
  rateModule: UbntRateModule;
  rateAuto: boolean;
  rateMcs: number;
  applyNow: boolean;
}

export interface UbntMutationResult {
  message: string;
  status: UbntWifiStatus;
}
