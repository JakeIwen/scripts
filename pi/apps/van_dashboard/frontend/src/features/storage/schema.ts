import * as v from 'valibot';

import {
  booleanValue,
  decode,
  nonnegativeInteger,
  nullableBoolean,
  nullableNumber,
  nullableOneOf,
  nullableString,
  object,
  oneOf,
  stringArray,
  text,
  trueValue,
} from '../../api/schema';

export const storagePolicyFieldSchema = oneOf([
  'disks_enabled',
  'torrents_enabled',
  'allow_starlink_torrents',
]);

const deferred = v.optional(v.unknown());
const versionOne = v.pipe(
  v.unknown(),
  v.check((value) => value === 1, 'must be 1'),
  v.literal(1),
);

const policyPreflightSchema = object({
  version: versionOne,
  runtime: object({
    mounted_disk_labels: stringArray,
    disks_mounted: booleanValue,
    qbittorrent_running: deferred,
  }),
  disks_enabled: deferred,
  torrents_enabled: deferred,
  allow_starlink_torrents: deferred,
});
const policyFieldsSchema = object({
  version: v.literal(1),
  disks_enabled: booleanValue,
  torrents_enabled: booleanValue,
  allow_starlink_torrents: booleanValue,
  runtime: object({
    disks_mounted: booleanValue,
    mounted_disk_labels: stringArray,
    qbittorrent_running: booleanValue,
  }),
});
const storagePolicySchema = v.pipe(
  policyPreflightSchema,
  v.check(
    (row) => row.runtime.disks_mounted === row.runtime.mounted_disk_labels.length > 0,
    'runtime mount state is inconsistent',
  ),
  v.transform((input): unknown => input),
  policyFieldsSchema,
  v.transform((row) => ({
    version: row.version,
    disksEnabled: row.disks_enabled,
    torrentsEnabled: row.torrents_enabled,
    allowStarlinkTorrents: row.allow_starlink_torrents,
    runtime: {
      disksMounted: row.runtime.disks_mounted,
      mountedDiskLabels: row.runtime.mounted_disk_labels,
      qbittorrentRunning: row.runtime.qbittorrent_running,
    },
  })),
);

export const storagePolicyResponseSchema = v.pipe(
  object({ ok: trueValue, policy: deferred }),
  // The envelope's ok check must precede policy version and runtime preflights.
  v.transform((row) => decode(storagePolicySchema, row.policy, 'policy')),
);

const diskHealthSchema = v.pipe(
  object({
    state: oneOf(['checking', 'healthy', 'warning', 'critical', 'unknown']),
    message: text,
    basis: oneOf(['mount', 'kernel_event', 'offline_check', 'history_unavailable', 'unverified']),
    observation: text,
    event_scope: nullableOneOf(['current_boot', 'cleared']),
    checked_at: nullableNumber,
    read_only: nullableBoolean,
    accessible: nullableBoolean,
    writable: nullableBoolean,
    recent_error_count: nonnegativeInteger,
    current_boot_error_count: nonnegativeInteger,
    previous_boot_error_count: nonnegativeInteger,
    historical_error_count: nonnegativeInteger,
    latest_error_at: nullableNumber,
    latest_error: nullableString,
    current_error_at: nullableNumber,
    current_error_message: nullableString,
    repairable: booleanValue,
  }),
  v.transform((row) => ({
    state: row.state,
    message: row.message,
    basis: row.basis,
    observation: row.observation,
    eventScope: row.event_scope,
    checkedAt: row.checked_at,
    readOnly: row.read_only,
    accessible: row.accessible,
    writable: row.writable,
    recentErrorCount: row.recent_error_count,
    currentBootErrorCount: row.current_boot_error_count,
    previousBootErrorCount: row.previous_boot_error_count,
    historicalErrorCount: row.historical_error_count,
    latestErrorAt: row.latest_error_at,
    latestError: row.latest_error,
    currentErrorAt: row.current_error_at,
    currentErrorMessage: row.current_error_message,
    repairable: row.repairable,
  })),
);

const managedDiskSchema = v.pipe(
  object({
    label: text,
    role: oneOf(['always', 'policy', 'backup']),
    automatic_mount: booleanValue,
    requires_disk_policy: booleanValue,
    controllable: booleanValue,
    attached: booleanValue,
    mounted: booleanValue,
    mountpoints: stringArray,
    device: nullableString,
    size_bytes: nullableNumber,
    filesystem: nullableString,
    expected_mount: text,
    hold_until: nullableNumber,
    hold_remaining_seconds: nullableNumber,
    error: nullableString,
    health: diskHealthSchema,
  }),
  v.transform((row) => ({
    label: row.label,
    role: row.role,
    automaticMount: row.automatic_mount,
    requiresDiskPolicy: row.requires_disk_policy,
    controllable: row.controllable,
    attached: row.attached,
    mounted: row.mounted,
    mountpoints: row.mountpoints,
    device: row.device,
    sizeBytes: row.size_bytes,
    filesystem: row.filesystem,
    expectedMount: row.expected_mount,
    holdUntil: row.hold_until,
    holdRemainingSeconds: row.hold_remaining_seconds,
    error: row.error,
    health: row.health,
  })),
);

const diskOperationSchema = v.pipe(
  object({
    status: oneOf(['idle', 'running', 'complete', 'error']),
    action: nullableOneOf(['eject', 'mount', 'repair']),
    label: nullableString,
    started_at: nullableNumber,
    completed_at: nullableNumber,
    error: nullableString,
  }),
  v.transform((row) => ({
    status: row.status,
    action: row.action,
    label: row.label,
    startedAt: row.started_at,
    completedAt: row.completed_at,
    error: row.error,
  })),
);

const diskStatusSchema = v.pipe(
  object({
    checked_at: nonnegativeInteger,
    disks: v.array(managedDiskSchema),
    operation: diskOperationSchema,
  }),
  v.transform((row) => ({
    checkedAt: row.checked_at,
    disks: row.disks,
    operation: row.operation,
  })),
);

export const diskStatusResponseSchema = v.pipe(
  object({ ok: trueValue, disk_status: deferred }),
  // The envelope's ok check must precede disk status fields and array items.
  v.transform((row) => decode(diskStatusSchema, row.disk_status, 'disk_status')),
);

export const storagePolicyMutationSchema = v.pipe(
  object({ ok: trueValue, message: text, policy: deferred }),
  v.transform((row) => ({
    message: row.message,
    // The legacy mutation validates ok/message before the policy response body.
    policy: decode(storagePolicyResponseSchema, row, 'storage policy response'),
  })),
);
export const diskMutationSchema = v.pipe(
  object({ ok: trueValue, message: text, disk_status: deferred }),
  v.transform((row) => ({
    message: row.message,
    // The legacy mutation validates ok/message before the disk status body.
    diskStatus: decode(diskStatusResponseSchema, row, 'disk status response'),
  })),
);

export function decodeStoragePolicyResponse(value: unknown): StoragePolicy {
  return decode(storagePolicyResponseSchema, value, 'storage policy response');
}
export function decodeDiskStatusResponse(value: unknown): DiskStatus {
  return decode(diskStatusResponseSchema, value, 'disk status response');
}
export function decodeStoragePolicyMutation(value: unknown): StoragePolicyMutationResult {
  return decode(storagePolicyMutationSchema, value, 'storage policy mutation response');
}
export function decodeDiskMutation(value: unknown): DiskMutationResult {
  return decode(diskMutationSchema, value, 'disk mutation response');
}

export type StoragePolicy = v.InferOutput<typeof storagePolicySchema>;
export type StoragePolicyRuntime = StoragePolicy['runtime'];
export type StoragePolicyField = v.InferOutput<typeof storagePolicyFieldSchema>;
export type StoragePolicyMutationResult = v.InferOutput<typeof storagePolicyMutationSchema>;
export type DiskRole = v.InferOutput<typeof managedDiskSchema>['role'];
export type DiskHealthState = v.InferOutput<typeof diskHealthSchema>['state'];
export type DiskHealthBasis = v.InferOutput<typeof diskHealthSchema>['basis'];
export type DiskEventScope = NonNullable<v.InferOutput<typeof diskHealthSchema>['eventScope']>;
export type DiskHealth = v.InferOutput<typeof diskHealthSchema>;
export type ManagedDisk = v.InferOutput<typeof managedDiskSchema>;
export type DiskOperationStatus = v.InferOutput<typeof diskOperationSchema>['status'];
export type DiskAction = NonNullable<v.InferOutput<typeof diskOperationSchema>['action']>;
export type DiskOperation = v.InferOutput<typeof diskOperationSchema>;
export type DiskStatus = v.InferOutput<typeof diskStatusSchema>;
export type DiskMutationResult = v.InferOutput<typeof diskMutationSchema>;
