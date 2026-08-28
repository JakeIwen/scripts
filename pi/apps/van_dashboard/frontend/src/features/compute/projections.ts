import type {
  ComputeJobState,
  ComputeJobSummary,
  ComputeLocalReason,
  ComputeSummary,
  ComputeTaskSummary,
  ComputeWorker,
  EligibleLocalWork,
} from './types';

type UnknownRecord = Record<string, unknown>;

/** Keep queue manifests and worker heartbeat internals on this side of the boundary. */
function record(value: unknown): UnknownRecord | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function nonnegativeNumber(value: unknown, fallback = 0): number {
  const number = finiteNumber(value);
  return number !== null && number >= 0 ? number : fallback;
}

function nonnegativeInteger(value: unknown, fallback = 0): number {
  const number = finiteNumber(value);
  return number !== null && Number.isInteger(number) && number >= 0 ? number : fallback;
}

function nullableNonnegativeInteger(value: unknown): number | null {
  const number = finiteNumber(value);
  return number !== null && Number.isInteger(number) && number >= 0 ? number : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function boolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

export function projectComputeWorkers(value: unknown): ComputeWorker[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((row) => {
    const worker = record(row);
    const name = text(worker?.worker);
    const available = boolean(worker?.available);
    if (!worker || !name || available === null) return [];
    return [
      {
        name,
        placement: text(worker.placement) ?? 'unknown',
        available,
        seenAt: finiteNumber(worker.seen_at),
        ageSeconds: finiteNumber(worker.age_seconds),
        slotsTotal: nullableNonnegativeInteger(worker.slots_total),
        slotsBusy: nullableNonnegativeInteger(worker.slots_busy),
        slotsAvailable: nullableNonnegativeInteger(worker.slots_available),
      },
    ];
  });
}

export function projectComputeSummary(value: unknown): ComputeSummary {
  const summary = record(value);
  if (!summary) throw new TypeError('compute summary must be an object');
  return {
    jobs: nonnegativeInteger(summary.jobs),
    succeeded: nonnegativeInteger(summary.succeeded),
    failed: nonnegativeInteger(summary.failed),
    telemetryJobs: nonnegativeInteger(summary.telemetry_jobs),
    macCpuSeconds: nonnegativeNumber(summary.mac_cpu_seconds),
    macWallSeconds: nonnegativeNumber(summary.mac_wall_seconds),
    peakResidentBytes: nonnegativeInteger(summary.peak_rss_bytes),
    inputBytes: nonnegativeInteger(summary.input_bytes),
    resultBytes: nonnegativeInteger(summary.result_bytes),
    averageQueueSeconds: finiteNumber(summary.average_queue_seconds),
    lastFinishedAt: finiteNumber(summary.last_finished_at),
  };
}

export function projectComputeTasks(value: unknown): ComputeTaskSummary[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((row) => {
    const task = record(row);
    const name = text(task?.task);
    if (!task || !name) return [];
    return [
      {
        task: name,
        jobs: nonnegativeInteger(task.jobs),
        succeeded: nonnegativeInteger(task.succeeded),
        failed: nonnegativeInteger(task.failed),
        telemetryJobs: nonnegativeInteger(task.telemetry_jobs),
        cpuSeconds: nonnegativeNumber(task.cpu_seconds),
        wallSeconds: nonnegativeNumber(task.wall_seconds),
        peakResidentBytes: nonnegativeInteger(task.peak_rss_bytes),
        inputBytes: nonnegativeInteger(task.input_bytes),
      },
    ];
  });
}

function jobState(value: unknown): ComputeJobState | null {
  return value === 'queued' || value === 'running' || value === 'done' || value === 'failed'
    ? value
    : null;
}

export function projectComputeJobs(value: unknown): ComputeJobSummary[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((row) => {
    const job = record(row);
    const id = text(job?.id);
    const task = text(job?.task);
    const state = jobState(job?.state);
    if (!job || !id || !task || !state) return [];
    return [
      {
        id,
        task,
        state,
        worker: text(job.worker),
        placement: text(job.placement) ?? 'unknown',
        failureSummary: text(job.failure_summary),
        submittedAt: finiteNumber(job.submitted_at),
        startedAt: finiteNumber(job.started_at),
        finishedAt: finiteNumber(job.finished_at),
        queueSeconds: finiteNumber(job.queue_seconds),
        activeSeconds: nonnegativeNumber(job.active_seconds ?? job.wall_seconds),
        cpuSeconds: nonnegativeNumber(job.cpu_seconds),
        peakResidentBytes: nonnegativeInteger(job.peak_rss_bytes),
        inputBytes: nonnegativeInteger(job.input_bytes),
        telemetryAvailable: boolean(job.telemetry) ?? false,
      },
    ];
  });
}

function projectLocalReasons(value: unknown): ComputeLocalReason[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((row) => {
    const reason = record(row);
    const name = text(reason?.reason);
    if (!reason || !name) return [];
    return [{ reason: name, events: nonnegativeInteger(reason.events) }];
  });
}

export function projectEligibleLocalWork(value: unknown): EligibleLocalWork {
  const local = record(value);
  return {
    events: nonnegativeInteger(local?.events),
    cpuSeconds: nonnegativeNumber(local?.cpu_seconds),
    wallSeconds: nonnegativeNumber(local?.wall_seconds),
    peakResidentBytes: nonnegativeInteger(local?.peak_rss_bytes),
    inputBytes: nonnegativeInteger(local?.input_bytes),
    reasons: projectLocalReasons(local?.reasons),
  };
}
