import {
  booleanValue,
  nullableBoolean,
  nullableNumber,
  numberValue,
  objectValue,
  optionalString,
} from '../../api/validation';
import {
  projectComputeJobs,
  projectComputeSummary,
  projectComputeTasks,
  projectComputeWorkers,
  projectEligibleLocalWork,
} from './projections';
import { COMPUTE_RANGES, type ComputeRange, type ComputeReport } from './types';

function trueValue(value: unknown, label: string): true {
  if (value !== true) throw new TypeError(`${label} must be true`);
  return true;
}

function nonnegativeInteger(value: unknown, label: string): number {
  const number = numberValue(value, label);
  if (!Number.isInteger(number) || number < 0) {
    throw new TypeError(`${label} must be a non-negative integer`);
  }
  return number;
}

function optionalNullableInteger(value: unknown, label: string): number | null {
  if (value === undefined) return null;
  const number = nullableNumber(value, label);
  if (number !== null && (!Number.isInteger(number) || number < 0)) {
    throw new TypeError(`${label} must be null or a non-negative integer`);
  }
  return number;
}

function computeRange(value: unknown): ComputeRange {
  const range = numberValue(value, 'compute range_hours');
  if (!COMPUTE_RANGES.includes(range as ComputeRange)) {
    throw new TypeError(`compute range_hours has an unsupported value: ${range}`);
  }
  return range as ComputeRange;
}

export function decodeComputeReport(value: unknown): ComputeReport {
  const response = objectValue(value, 'compute response');
  trueValue(response.ok, 'compute response.ok');
  const status = objectValue(response.status, 'compute status');

  return {
    generatedAt:
      response.generated_at === undefined
        ? null
        : nullableNumber(response.generated_at, 'compute generated_at'),
    rangeHours: computeRange(response.range_hours),
    status: {
      configured:
        status.configured === undefined
          ? null
          : nullableBoolean(status.configured, 'compute status.configured'),
      available: booleanValue(status.available, 'compute status.available'),
      queued: nonnegativeInteger(status.queued, 'compute status.queued'),
      running: nonnegativeInteger(status.running, 'compute status.running'),
      localRunning:
        status.local_running === undefined
          ? 0
          : nonnegativeInteger(status.local_running, 'compute status.local_running'),
      slotsTotal: optionalNullableInteger(status.slots_total, 'compute status.slots_total'),
      slotsBusy: optionalNullableInteger(status.slots_busy, 'compute status.slots_busy'),
      slotsAvailable: optionalNullableInteger(
        status.slots_available,
        'compute status.slots_available',
      ),
      workers: projectComputeWorkers(status.workers),
    },
    summary: projectComputeSummary(response.summary),
    tasks: projectComputeTasks(response.tasks),
    jobs: projectComputeJobs(response.jobs),
    eligibleLocalWork: projectEligibleLocalWork(response.eligible_local_work),
    measurementNote: optionalString(response.measurement_note, 'compute measurement_note') ?? null,
  };
}
