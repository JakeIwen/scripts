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
  DiskAction,
  DiskEventScope,
  DiskHealth,
  DiskHealthBasis,
  DiskHealthState,
  DiskOperation,
  DiskOperationStatus,
  DiskRole,
  DiskStatus,
  ManagedDisk,
  StoragePolicy,
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

export function decodeStoragePolicyResponse(value: unknown): StoragePolicy {
  const response = objectValue(value, 'storage policy response');
  trueValue(response.ok, 'storage policy response.ok');
  const policy = objectValue(response.policy, 'policy');
  if (policy.version !== 1) throw new TypeError('policy.version must be 1');
  const runtime = objectValue(policy.runtime, 'policy.runtime');
  const mountedDiskLabels = stringArray(
    runtime.mounted_disk_labels,
    'policy.runtime.mounted_disk_labels',
  );
  const disksMounted = booleanValue(runtime.disks_mounted, 'policy.runtime.disks_mounted');
  if (disksMounted !== mountedDiskLabels.length > 0) {
    throw new TypeError('policy runtime mount state is inconsistent');
  }

  return {
    version: 1,
    disksEnabled: booleanValue(policy.disks_enabled, 'policy.disks_enabled'),
    torrentsEnabled: booleanValue(policy.torrents_enabled, 'policy.torrents_enabled'),
    allowStarlinkTorrents: booleanValue(
      policy.allow_starlink_torrents,
      'policy.allow_starlink_torrents',
    ),
    runtime: {
      disksMounted,
      mountedDiskLabels,
      qbittorrentRunning: booleanValue(
        runtime.qbittorrent_running,
        'policy.runtime.qbittorrent_running',
      ),
    },
  };
}

const HEALTH_STATES = [
  'checking',
  'healthy',
  'warning',
  'critical',
  'unknown',
] as const satisfies readonly DiskHealthState[];
const HEALTH_BASES = [
  'mount',
  'kernel_event',
  'offline_check',
  'history_unavailable',
  'unverified',
] as const satisfies readonly DiskHealthBasis[];
const EVENT_SCOPES = ['current_boot', 'cleared'] as const satisfies readonly DiskEventScope[];

function decodeHealth(value: unknown, label: string): DiskHealth {
  const object = objectValue(value, label);
  return {
    state: oneOf(object.state, HEALTH_STATES, `${label}.state`),
    message: stringValue(object.message, `${label}.message`),
    basis: oneOf(object.basis, HEALTH_BASES, `${label}.basis`),
    observation: stringValue(object.observation, `${label}.observation`),
    eventScope: nullableOneOf(object.event_scope, EVENT_SCOPES, `${label}.event_scope`),
    checkedAt: nullableNumber(object.checked_at, `${label}.checked_at`),
    readOnly: nullableBoolean(object.read_only, `${label}.read_only`),
    accessible: nullableBoolean(object.accessible, `${label}.accessible`),
    writable: nullableBoolean(object.writable, `${label}.writable`),
    recentErrorCount: nonnegativeInteger(object.recent_error_count, `${label}.recent_error_count`),
    currentBootErrorCount: nonnegativeInteger(
      object.current_boot_error_count,
      `${label}.current_boot_error_count`,
    ),
    previousBootErrorCount: nonnegativeInteger(
      object.previous_boot_error_count,
      `${label}.previous_boot_error_count`,
    ),
    historicalErrorCount: nonnegativeInteger(
      object.historical_error_count,
      `${label}.historical_error_count`,
    ),
    latestErrorAt: nullableNumber(object.latest_error_at, `${label}.latest_error_at`),
    latestError: nullableString(object.latest_error, `${label}.latest_error`),
    currentErrorAt: nullableNumber(object.current_error_at, `${label}.current_error_at`),
    currentErrorMessage: nullableString(
      object.current_error_message,
      `${label}.current_error_message`,
    ),
    repairable: booleanValue(object.repairable, `${label}.repairable`),
  };
}

const DISK_ROLES = ['always', 'policy', 'backup'] as const satisfies readonly DiskRole[];

function decodeDisk(value: unknown, index: number): ManagedDisk {
  const label = `disk_status.disks[${index}]`;
  const object = objectValue(value, label);
  return {
    label: stringValue(object.label, `${label}.label`),
    role: oneOf(object.role, DISK_ROLES, `${label}.role`),
    automaticMount: booleanValue(object.automatic_mount, `${label}.automatic_mount`),
    requiresDiskPolicy: booleanValue(object.requires_disk_policy, `${label}.requires_disk_policy`),
    controllable: booleanValue(object.controllable, `${label}.controllable`),
    attached: booleanValue(object.attached, `${label}.attached`),
    mounted: booleanValue(object.mounted, `${label}.mounted`),
    mountpoints: stringArray(object.mountpoints, `${label}.mountpoints`),
    device: nullableString(object.device, `${label}.device`),
    sizeBytes: nullableNumber(object.size_bytes, `${label}.size_bytes`),
    filesystem: nullableString(object.filesystem, `${label}.filesystem`),
    expectedMount: stringValue(object.expected_mount, `${label}.expected_mount`),
    holdUntil: nullableNumber(object.hold_until, `${label}.hold_until`),
    holdRemainingSeconds: nullableNumber(
      object.hold_remaining_seconds,
      `${label}.hold_remaining_seconds`,
    ),
    error: nullableString(object.error, `${label}.error`),
    health: decodeHealth(object.health, `${label}.health`),
  };
}

const OPERATION_STATUSES = [
  'idle',
  'running',
  'complete',
  'error',
] as const satisfies readonly DiskOperationStatus[];
const DISK_ACTIONS = ['eject', 'mount', 'repair'] as const satisfies readonly DiskAction[];

function decodeOperation(value: unknown): DiskOperation {
  const object = objectValue(value, 'disk_status.operation');
  return {
    status: oneOf(object.status, OPERATION_STATUSES, 'disk_status.operation.status'),
    action: nullableOneOf(object.action, DISK_ACTIONS, 'disk_status.operation.action'),
    label: nullableString(object.label, 'disk_status.operation.label'),
    startedAt: nullableNumber(object.started_at, 'disk_status.operation.started_at'),
    completedAt: nullableNumber(object.completed_at, 'disk_status.operation.completed_at'),
    error: nullableString(object.error, 'disk_status.operation.error'),
  };
}

export function decodeDiskStatusResponse(value: unknown): DiskStatus {
  const response = objectValue(value, 'disk status response');
  trueValue(response.ok, 'disk status response.ok');
  const status = objectValue(response.disk_status, 'disk_status');
  return {
    checkedAt: nonnegativeInteger(status.checked_at, 'disk_status.checked_at'),
    disks: arrayValue(status.disks, 'disk_status.disks').map(decodeDisk),
    operation: decodeOperation(status.operation),
  };
}
