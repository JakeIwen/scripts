import {
  arrayValue,
  booleanValue,
  nullableNumber,
  nullableString,
  numberValue,
  objectValue,
  stringValue,
} from '../../api/validation';
import type {
  BackupEvidence,
  BackupHealth,
  BackupOperation,
  BackupOperationKind,
  BackupOperationStatus,
  BackupProgress,
  BackupStatus,
  BackupSettings,
  BackupStopKind,
  BackupStopOperation,
  BackupStopStatus,
  HotspareStatus,
  CloneCardNominalGb,
  TimeMachineStatus,
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

function numberArray(value: unknown, label: string): number[] {
  return arrayValue(value, label).map((item, index) =>
    nonnegativeInteger(item, `${label}[${index}]`),
  );
}

const CLONE_CARD_NOMINAL_GB_OPTIONS = [32, 64, 128, 256] as const;

function cloneCardNominalGb(value: unknown, label: string): CloneCardNominalGb {
  const number = nonnegativeInteger(value, label);
  if (!CLONE_CARD_NOMINAL_GB_OPTIONS.includes(number as CloneCardNominalGb)) {
    throw new TypeError(`${label} has an unsupported value: ${number}`);
  }
  return number as CloneCardNominalGb;
}

function decodeSettings(value: unknown): BackupSettings {
  const object = objectValue(value, 'backups.settings');
  const options = arrayValue(
    object.clone_card_nominal_gb_options,
    'backups.settings.clone_card_nominal_gb_options',
  ).map((item, index) =>
    cloneCardNominalGb(item, `backups.settings.clone_card_nominal_gb_options[${index}]`),
  );
  const selected = cloneCardNominalGb(
    object.clone_card_nominal_gb,
    'backups.settings.clone_card_nominal_gb',
  );
  if (!options.includes(selected)) {
    throw new TypeError('backups.settings must include the selected clone card capacity');
  }
  return {
    cloneCardNominalGb: selected,
    rootUsedMaxGib: nonnegativeInteger(
      object.root_used_max_gib,
      'backups.settings.root_used_max_gib',
    ),
    cloneCardNominalGbOptions: options,
  };
}

function decodeProgress(value: unknown, label: string): BackupProgress | null {
  if (value === null) return null;
  const object = objectValue(value, label);
  return {
    phase: stringValue(object.phase, `${label}.phase`),
    detail: stringValue(object.detail, `${label}.detail`),
    startedAt: nonnegativeInteger(object.started_at, `${label}.started_at`),
    updatedAt: nonnegativeInteger(object.updated_at, `${label}.updated_at`),
    elapsedSeconds: nonnegativeInteger(object.elapsed_seconds, `${label}.elapsed_seconds`),
    bytesProcessed: optionalNullableNumber(object.bytes_processed, `${label}.bytes_processed`),
    progressPercent: optionalNullableNumber(object.progress_percent, `${label}.progress_percent`),
    filesTransferred: optionalNullableNumber(
      object.files_transferred,
      `${label}.files_transferred`,
    ),
    filesRemaining: optionalNullableNumber(object.files_remaining, `${label}.files_remaining`),
    fileListTotal: optionalNullableNumber(object.file_list_total, `${label}.file_list_total`),
  };
}

function decodeEvidence(value: unknown, label: string): BackupEvidence {
  const object = objectValue(value, label);
  return {
    lastSuccessAt: nullableNumber(object.last_success_at, `${label}.last_success_at`),
    staleHours: nonnegativeInteger(object.stale_hours, `${label}.stale_hours`),
    stale: booleanValue(object.stale, `${label}.stale`),
    running: booleanValue(object.running, `${label}.running`),
    progress: decodeProgress(object.progress, `${label}.progress`),
  };
}

function decodeHotspare(value: unknown, index: number): HotspareStatus {
  const label = `backups.hotswaps[${index}]`;
  const object = objectValue(value, label);
  return {
    label: stringValue(object.label, `${label}.label`),
    intervalDays: nonnegativeInteger(object.interval_days, `${label}.interval_days`),
    attached: booleanValue(object.attached, `${label}.attached`),
    device: nullableString(object.device, `${label}.device`),
    sizeBytes: nullableNumber(object.size_bytes, `${label}.size_bytes`),
    mounted: booleanValue(object.mounted, `${label}.mounted`),
    mountpoints: stringArray(object.mountpoints, `${label}.mountpoints`),
    lastCloneAt: nullableNumber(object.last_clone_at, `${label}.last_clone_at`),
    due: booleanValue(object.due, `${label}.due`),
    stale: booleanValue(object.stale, `${label}.stale`),
  };
}

function decodeTimeMachine(value: unknown): TimeMachineStatus {
  const object = objectValue(value, 'backups.time_machine');
  return {
    device: stringValue(object.device, 'backups.time_machine.device'),
    available: booleanValue(object.available, 'backups.time_machine.available'),
    lastBackupAt: nullableNumber(object.last_backup_at, 'backups.time_machine.last_backup_at'),
    snapshots: numberArray(object.snapshots, 'backups.time_machine.snapshots'),
    running: booleanValue(object.running, 'backups.time_machine.running'),
    progressPercent: nullableNumber(
      object.progress_percent,
      'backups.time_machine.progress_percent',
    ),
    bytesCopied: nullableNumber(object.bytes_copied, 'backups.time_machine.bytes_copied'),
    totalBytes: nullableNumber(object.total_bytes, 'backups.time_machine.total_bytes'),
    updatedAt: nullableNumber(object.updated_at, 'backups.time_machine.updated_at'),
    error: nullableString(object.error, 'backups.time_machine.error'),
  };
}

const OPERATION_STATUSES = [
  'idle',
  'running',
  'complete',
  'error',
  'stopped',
] as const satisfies readonly BackupOperationStatus[];
const OPERATION_KINDS = [
  'clone',
  'borg',
  'exfat',
] as const satisfies readonly BackupOperationKind[];

function decodeOperation(value: unknown): BackupOperation {
  const object = objectValue(value, 'backups.operation');
  return {
    status: oneOf(object.status, OPERATION_STATUSES, 'backups.operation.status'),
    kind: nullableOneOf(object.kind, OPERATION_KINDS, 'backups.operation.kind'),
    target: nullableString(object.target, 'backups.operation.target'),
    startedAt: nullableNumber(object.started_at, 'backups.operation.started_at'),
    completedAt: nullableNumber(object.completed_at, 'backups.operation.completed_at'),
    error: nullableString(object.error, 'backups.operation.error'),
  };
}

const STOP_STATUSES = [
  'idle',
  'running',
  'complete',
  'error',
] as const satisfies readonly BackupStopStatus[];
const STOP_KINDS = ['borg', 'exfat'] as const satisfies readonly BackupStopKind[];

function decodeStopOperation(value: unknown): BackupStopOperation {
  const object = objectValue(value, 'backups.stop');
  return {
    status: oneOf(object.status, STOP_STATUSES, 'backups.stop.status'),
    kind: nullableOneOf(object.kind, STOP_KINDS, 'backups.stop.kind'),
    startedAt: nullableNumber(object.started_at, 'backups.stop.started_at'),
    completedAt: nullableNumber(object.completed_at, 'backups.stop.completed_at'),
    error: nullableString(object.error, 'backups.stop.error'),
  };
}

const HEALTH_STATES = ['running', 'attention', 'good'] as const satisfies readonly BackupHealth[];

export function decodeBackupStatusResponse(value: unknown): BackupStatus {
  const response = objectValue(value, 'backup status response');
  trueValue(response.ok, 'backup status response.ok');
  const backups = objectValue(response.backups, 'backups');
  return {
    checkedAt: nonnegativeInteger(backups.checked_at, 'backups.checked_at'),
    health: oneOf(backups.health, HEALTH_STATES, 'backups.health'),
    settings: decodeSettings(backups.settings),
    borg: decodeEvidence(backups.borg, 'backups.borg'),
    exfatSnapshot: decodeEvidence(backups.exfat_snapshot, 'backups.exfat_snapshot'),
    openwrt: decodeEvidence(backups.openwrt, 'backups.openwrt'),
    hotswaps: arrayValue(backups.hotswaps, 'backups.hotswaps').map(decodeHotspare),
    timeMachine: decodeTimeMachine(backups.time_machine),
    operation: decodeOperation(backups.operation),
    stop: decodeStopOperation(backups.stop),
  };
}
