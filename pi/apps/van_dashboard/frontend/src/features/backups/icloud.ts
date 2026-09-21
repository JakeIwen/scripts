import {
  arrayValue,
  booleanValue,
  nullableString,
  objectValue,
  stringValue,
  numberValue,
} from '../../api/validation';

export const ICLOUD_PHASES = [
  'checking',
  'preparing',
  'uploading',
  'verifying',
  'publishing',
  'retention',
  'complete',
  'not due',
  'deferred',
  'error',
  'authentication_required',
  'interrupted',
  'unavailable',
] as const;
export type ICloudPhase = (typeof ICLOUD_PHASES)[number];
export interface ICloudProgress {
  uploadEstimatedBytes: number | null;
  uploadTotalBytes: number | null;
  commandBytes: number | null;
  verifiedBytes: number | null;
  verificationTotalBytes: number | null;
  verifiedFiles: number | null;
  verificationTotalFiles: number | null;
  currentFileBytes: number | null;
}
export interface ICloudAttempt {
  id: string;
  startedAt: number;
  endedAt: number | null;
  phase: ICloudPhase;
  workPhase: ICloudPhase;
  generation: string | null;
  message: string;
  progress: ICloudProgress;
  verifiedAt: number | null;
}
export interface AvailableICloudStatus {
  available: true;
  running: boolean;
  phase: ICloudPhase;
  message: string;
  lastWorkPhase: ICloudPhase;
  generation: string | null;
  lastSuccessAt: number | null;
  nextCheckAt: number | null;
  nextDueAt: number | null;
  updatedAt: number | null;
  progressStale: boolean;
  attention: boolean;
  intervalDays: number;
  keepGenerations: number;
  progress: ICloudProgress;
  historyStartedAt: number | null;
  attempts: ICloudAttempt[];
  verifiedGenerations: { generation: string; completedAt: number }[];
}
export type ICloudStatus = AvailableICloudStatus | { available: false; running: false };

function numeric(value: unknown): number {
  const result = numberValue(value, 'iCloud counter');
  if (result < 0) throw new TypeError('iCloud counter must be nonnegative');
  return result;
}
function nullableNumeric(value: unknown): number | null {
  return value === null ? null : numeric(value);
}
function phase(value: unknown): ICloudPhase {
  const result = stringValue(value, 'iCloud phase') as ICloudPhase;
  if (!ICLOUD_PHASES.includes(result)) throw new TypeError('Unsupported iCloud phase');
  return result;
}
function progress(value: unknown): ICloudProgress {
  const row = objectValue(value, 'iCloud progress');
  return {
    uploadEstimatedBytes: nullableNumeric(row.upload_estimated_bytes),
    uploadTotalBytes: nullableNumeric(row.upload_total_bytes),
    commandBytes: nullableNumeric(row.command_bytes),
    verifiedBytes: nullableNumeric(row.verified_bytes),
    verificationTotalBytes: nullableNumeric(row.verification_total_bytes),
    verifiedFiles: nullableNumeric(row.verified_files),
    verificationTotalFiles: nullableNumeric(row.verification_total_files),
    currentFileBytes: nullableNumeric(row.current_file_bytes),
  };
}
export function decodeICloud(value: unknown): ICloudStatus | null {
  if (value === undefined || value === null) return null; // Rolling backend deployments.
  const row = objectValue(value, 'iCloud');
  if (!booleanValue(row.available, 'iCloud.available')) return { available: false, running: false };
  return {
    available: true,
    running: booleanValue(row.running, 'iCloud.running'),
    phase: phase(row.phase),
    message: stringValue(row.message, 'iCloud.message'),
    lastWorkPhase: phase(row.last_work_phase),
    generation: nullableString(row.generation, 'iCloud.generation'),
    lastSuccessAt: nullableNumeric(row.last_success_at),
    nextCheckAt: nullableNumeric(row.next_check_at),
    nextDueAt: nullableNumeric(row.next_due_at),
    updatedAt: nullableNumeric(row.updated_at),
    progressStale: booleanValue(row.progress_stale, 'iCloud.progress_stale'),
    attention: booleanValue(row.attention, 'iCloud.attention'),
    intervalDays: numeric(row.interval_days),
    keepGenerations: numeric(row.keep_generations),
    progress: progress(row.progress),
    historyStartedAt: nullableNumeric(row.history_started_at),
    attempts: arrayValue(row.attempts, 'iCloud.attempts').map((value) => {
      const attempt = objectValue(value, 'iCloud attempt');
      return {
        id: stringValue(attempt.id, 'attempt.id'),
        startedAt: numeric(attempt.started_at),
        endedAt: nullableNumeric(attempt.ended_at),
        phase: phase(attempt.phase),
        workPhase: phase(attempt.work_phase),
        generation: nullableString(attempt.generation, 'attempt.generation'),
        message: stringValue(attempt.message, 'attempt.message'),
        progress: progress(attempt.progress),
        verifiedAt: nullableNumeric(attempt.verified_at),
      };
    }),
    verifiedGenerations: arrayValue(row.verified_generations, 'iCloud.verified_generations').map(
      (value) => {
        const entry = objectValue(value, 'iCloud verified generation');
        return {
          generation: stringValue(entry.generation, 'generation'),
          completedAt: numeric(entry.completed_at),
        };
      },
    ),
  };
}

export function iCloudPhaseLabel(value: ICloudPhase): string {
  return {
    checking: 'Checking',
    preparing: 'Preparing',
    uploading: 'Uploading',
    verifying: 'Verifying',
    publishing: 'Finalizing',
    retention: 'Verified · cleanup',
    complete: 'Verified',
    'not due': 'Scheduled',
    deferred: 'Paused',
    error: 'Failed',
    authentication_required: 'Sign-in needed',
    interrupted: 'Interrupted',
    unavailable: 'Unavailable',
  }[value];
}

export function iCloudProgress(status: AvailableICloudStatus) {
  const p = status.progress;
  const verification = ['verifying', 'publishing', 'retention', 'complete'].includes(
    status.running ? status.phase : status.lastWorkPhase,
  );
  const done = verification ? p.verifiedBytes : p.uploadEstimatedBytes;
  const total = verification ? p.verificationTotalBytes : p.uploadTotalBytes;
  return {
    verification,
    done,
    total,
    percent:
      done !== null && total !== null && total > 0 ? Math.min(100, (done / total) * 100) : null,
  };
}
