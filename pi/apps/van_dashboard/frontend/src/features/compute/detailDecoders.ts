import {
  booleanValue,
  nullableNumber,
  nullableString,
  numberValue,
  objectValue,
  stringValue,
} from '../../api/validation';
import { projectComputeJobs } from './projections';
import {
  COMPUTE_RANGES,
  type ComputeJobDetails,
  type ComputeOutputExcerpt,
  type ComputeRange,
  type ComputeTaskJobs,
} from './types';

export const COMPUTE_JOB_ID_PATTERN = /^\d{8}T\d{6}Z-[0-9a-f]{8}$/;
export const COMPUTE_TASK_PATTERN = /^[a-z0-9-]{1,64}$/;

function trueValue(value: unknown, label: string): true {
  if (value !== true) throw new TypeError(`${label} must be true`);
  return true;
}

function nonnegativeInteger(value: unknown, label: string): number {
  const number = numberValue(value, label);
  if (!Number.isSafeInteger(number) || number < 0) {
    throw new TypeError(`${label} must be a non-negative integer`);
  }
  return number;
}

function rangeValue(value: unknown): ComputeRange {
  const range = numberValue(value, 'compute task jobs.range_hours');
  if (!COMPUTE_RANGES.includes(range as ComputeRange)) {
    throw new TypeError(`compute task jobs.range_hours has an unsupported value: ${range}`);
  }
  return range as ComputeRange;
}

function outputExcerpt(value: unknown, label: string): ComputeOutputExcerpt {
  const output = objectValue(value, label);
  const from = stringValue(output.excerpt_from, `${label}.excerpt_from`);
  if (from !== 'none' && from !== 'full' && from !== 'tail') {
    throw new TypeError(`${label}.excerpt_from has an unsupported value: ${from}`);
  }
  return {
    available: booleanValue(output.available, `${label}.available`),
    bytes: nonnegativeInteger(output.bytes, `${label}.bytes`),
    excerpt: stringValue(output.excerpt, `${label}.excerpt`),
    truncated: booleanValue(output.truncated, `${label}.truncated`),
    excerptFrom: from,
  };
}

export function decodeComputeTaskJobs(value: unknown): ComputeTaskJobs {
  const response = objectValue(value, 'compute task jobs response');
  trueValue(response.ok, 'compute task jobs response.ok');
  const task = stringValue(response.task, 'compute task jobs.task');
  if (!COMPUTE_TASK_PATTERN.test(task)) throw new TypeError('compute task jobs.task is invalid');
  const jobs = projectComputeJobs(response.jobs);
  if (jobs.some((job) => job.task !== task))
    throw new TypeError('compute task jobs contains another task');
  const matchingJobs = nonnegativeInteger(
    response.matching_jobs,
    'compute task jobs.matching_jobs',
  );
  const truncated = booleanValue(response.truncated, 'compute task jobs.truncated');
  if (jobs.length > matchingJobs || (!truncated && jobs.length !== matchingJobs)) {
    throw new TypeError('compute task job count is inconsistent');
  }
  return { rangeHours: rangeValue(response.range_hours), task, matchingJobs, truncated, jobs };
}

export function decodeComputeJobDetails(value: unknown): ComputeJobDetails {
  const response = objectValue(value, 'compute job details response');
  trueValue(response.ok, 'compute job details response.ok');
  const rawJob = objectValue(response.job, 'compute job details.job');
  const jobs = projectComputeJobs([rawJob]);
  if (jobs.length !== 1 || !COMPUTE_JOB_ID_PATTERN.test(jobs[0]!.id)) {
    throw new TypeError('compute job details contains an invalid job');
  }
  const diagnostics = objectValue(response.diagnostics, 'compute job details.diagnostics');
  const truncated = objectValue(diagnostics.truncated, 'compute job details.diagnostics.truncated');
  const diagnostic = (field: string) => ({
    text: nullableString(diagnostics[field], `compute diagnostics.${field}`),
    truncated: booleanValue(truncated[field], `compute diagnostics.truncated.${field}`),
  });
  const exitCode = nullableNumber(rawJob.exit_code, 'compute job details.job.exit_code');
  if (exitCode !== null && !Number.isInteger(exitCode))
    throw new TypeError('compute job exit code must be an integer');
  return {
    job: jobs[0]!,
    exitCode,
    diagnostics: {
      failureClassification: nullableString(
        diagnostics.failure_classification,
        'compute diagnostics.failure_classification',
      ),
      workerError: diagnostic('worker_error'),
      resourceLimit: diagnostic('resource_limit'),
      resourceMonitorError: diagnostic('resource_monitor_error'),
      timedOut: booleanValue(diagnostics.timed_out, 'compute diagnostics.timed_out'),
      interrupted: booleanValue(diagnostics.interrupted, 'compute diagnostics.interrupted'),
    },
    stdout: outputExcerpt(response.stdout, 'compute job details.stdout'),
    stderr: outputExcerpt(response.stderr, 'compute job details.stderr'),
  };
}
