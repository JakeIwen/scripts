export type UsbDeviceStatus = 'unplugged' | 'partial' | 'root' | 'present';
export type UsbDeviceEventKind = 'plugged' | 'replugged' | 'unplugged';

export interface UsbTopologyInstance {
  deviceNumber: number;
  location: string;
  parentLocation: string;
  port: number;
  labels: string[];
}

export interface UsbDeviceEvent {
  kind: UsbDeviceEventKind;
  at: number;
}

export interface UsbDevice {
  bus: string;
  deviceId: string;
  description: string;
  presentCount: number;
  labels: string[];
  rootHub: boolean;
  instances: UsbTopologyInstance[];
  maxCount: number;
  knownInstances: UsbTopologyInstance[];
  event: UsbDeviceEvent | null;
  status: UsbDeviceStatus;
}

export interface UsbInventory {
  checkedAt: number | null;
  lastSuccessAt: number | null;
  lastError: string | null;
  presentDeviceCount: number;
  unpluggedDeviceCount: number;
  storageLabels: string[];
  devices: UsbDevice[];
}

export type UsbPortMethod = 'power' | 'disable';
export type UsbPortAction = 'on' | 'off' | 'cycle' | 'restore';
export type UsbPortOperationStatus = 'idle' | 'running' | 'complete' | 'error';

export interface UsbPort {
  key: string;
  location: string;
  port: number;
  method: UsbPortMethod;
  enabled: boolean | null;
  deviceDescriptions: string[];
  downstreamDeviceCount: number;
  storageLabels: string[];
  mountedLabels: string[];
  topologyLocations: string[];
}

export interface UsbHub {
  location: string;
  description: string;
  detail: string | null;
  deviceId: string | null;
  method: UsbPortMethod;
  physical: boolean;
  advanced: boolean;
  ports: UsbPort[];
}

export interface UsbPortOperation {
  status: UsbPortOperationStatus;
  key: string | null;
  action: UsbPortAction | null;
  startedAt: number | null;
  completedAt: number | null;
  message: string | null;
  error: string | null;
}

export interface UsbPortState {
  loaded: boolean;
  checkedAt: number | null;
  expiresAt: number | null;
  lastError: string | null;
  hubs: UsbHub[];
  operation: UsbPortOperation;
  expired: boolean;
}

export interface UsbStatus {
  inventory: UsbInventory;
  ports: UsbPortState;
}

export interface UsbPortMutationResult {
  message: string;
  ports: UsbPortState;
}
