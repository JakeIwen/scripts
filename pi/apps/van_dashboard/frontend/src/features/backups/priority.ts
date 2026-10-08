import { getJson, postForm } from '../../api/client';
import {
  arrayValue,
  booleanValue,
  nullableString,
  numberValue,
  objectValue,
  stringValue,
} from '../../api/validation';
import type { ICloudBackupKind } from './icloud';

export const PRIORITY_MODES = ['normal', 'pi', 'time-machine'] as const;
export type BackupPriorityMode = (typeof PRIORITY_MODES)[number];
const CAPTURE_PHASES = ['none', 'pending', 'claimed', 'expired', 'complete'] as const;
export interface BackupPriorityStatus {
  mode: BackupPriorityMode;
  requestedAt: number | null;
  completed: boolean;
  jobs: {
    kind: ICloudBackupKind;
    label: string;
    eligible: boolean;
    reason: string | null;
    lastSuccessAt: number | null;
    waiting: boolean;
  }[];
  capture: {
    coordinatorReady: boolean;
    captureComplete: boolean;
    phase: (typeof CAPTURE_PHASES)[number];
    expiresAt: number | null;
  };
}

function mode(value: unknown): BackupPriorityMode {
  const result = stringValue(value, 'backup priority') as BackupPriorityMode;
  if (!PRIORITY_MODES.includes(result)) throw new TypeError('Unsupported backup priority');
  return result;
}

const timestamp = (value: unknown) =>
  value === null ? null : numberValue(value, 'backup priority time');

export async function fetchBackupPriority(signal: AbortSignal): Promise<BackupPriorityStatus> {
  const response = objectValue(
    await getJson('/api/backups/priority', signal),
    'backup priority response',
  );
  const row = objectValue(response.priority, 'backup priority');
  const capture = objectValue(row.capture, 'Mac capture priority');
  const phase = stringValue(
    capture.phase,
    'capture request phase',
  ) as BackupPriorityStatus['capture']['phase'];
  if (!CAPTURE_PHASES.includes(phase)) throw new TypeError('Unsupported capture request phase');
  return {
    mode: mode(row.mode),
    requestedAt: timestamp(row.requested_at),
    completed: booleanValue(row.completed, 'priority completed'),
    jobs: arrayValue(row.jobs, 'priority jobs').map((value) => {
      const job = objectValue(value, 'priority job');
      const kind = mode(job.kind);
      if (kind === 'normal') throw new TypeError('Unsupported priority job');
      return {
        kind,
        label: stringValue(job.label, 'priority job label'),
        eligible: booleanValue(job.eligible, 'priority eligibility'),
        reason: nullableString(job.reason, 'priority eligibility reason'),
        lastSuccessAt: timestamp(job.last_success_at),
        waiting: booleanValue(job.waiting, 'priority waiting'),
      };
    }),
    capture: {
      coordinatorReady: booleanValue(capture.coordinator_ready, 'Mac coordinator ready'),
      captureComplete: booleanValue(capture.capture_complete, 'Mac capture complete'),
      phase,
      expiresAt: timestamp(capture.expires_at),
    },
  };
}

export const selectBackupPriority = (selected: BackupPriorityMode) =>
  postForm('/api/backups/priority', { mode: selected });
export const requestMacCapture = (cancel = false) =>
  postForm(`/api/backups/priority/capture${cancel ? '/cancel' : ''}`);
