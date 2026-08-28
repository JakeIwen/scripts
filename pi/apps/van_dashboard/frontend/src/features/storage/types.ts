import type { StatusTone } from '../../components/StatusPill';
import type { UsbPort } from '../usb/types';

export interface StoragePolicyRuntime {
  disksMounted: boolean;
  mountedDiskLabels: string[];
  qbittorrentRunning: boolean;
}

export interface StoragePolicy {
  version: 1;
  disksEnabled: boolean;
  torrentsEnabled: boolean;
  allowStarlinkTorrents: boolean;
  runtime: StoragePolicyRuntime;
}

export type StoragePolicyField = 'disks_enabled' | 'torrents_enabled' | 'allow_starlink_torrents';

export interface StoragePolicyMutationResult {
  message: string;
  policy: StoragePolicy;
}

export type DiskRole = 'always' | 'policy' | 'backup';
export type DiskOperationStatus = 'idle' | 'running' | 'complete' | 'error';
export type DiskAction = 'eject' | 'mount' | 'repair';
export type DiskHealthState = 'checking' | 'healthy' | 'warning' | 'critical' | 'unknown';
export type DiskHealthBasis =
  'mount' | 'kernel_event' | 'offline_check' | 'history_unavailable' | 'unverified';
export type DiskEventScope = 'current_boot' | 'cleared';

export interface DiskHealth {
  state: DiskHealthState;
  message: string;
  basis: DiskHealthBasis;
  observation: string;
  eventScope: DiskEventScope | null;
  checkedAt: number | null;
  readOnly: boolean | null;
  accessible: boolean | null;
  writable: boolean | null;
  recentErrorCount: number;
  currentBootErrorCount: number;
  previousBootErrorCount: number;
  historicalErrorCount: number;
  latestErrorAt: number | null;
  latestError: string | null;
  currentErrorAt: number | null;
  currentErrorMessage: string | null;
  repairable: boolean;
}

export interface ManagedDisk {
  label: string;
  role: DiskRole;
  automaticMount: boolean;
  requiresDiskPolicy: boolean;
  controllable: boolean;
  attached: boolean;
  mounted: boolean;
  mountpoints: string[];
  device: string | null;
  sizeBytes: number | null;
  filesystem: string | null;
  expectedMount: string;
  holdUntil: number | null;
  holdRemainingSeconds: number | null;
  error: string | null;
  health: DiskHealth;
}

export interface DiskOperation {
  status: DiskOperationStatus;
  action: DiskAction | null;
  label: string | null;
  startedAt: number | null;
  completedAt: number | null;
  error: string | null;
}

export interface DiskStatus {
  checkedAt: number;
  disks: ManagedDisk[];
  operation: DiskOperation;
}

export interface DiskMutationResult {
  message: string;
  diskStatus: DiskStatus;
}

export interface UsbResetAssessment {
  eligible: boolean;
  tone: StatusTone;
  label: string;
  detail: string;
  port: UsbPort | null;
}
