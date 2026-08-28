import { getJson } from '../../api/client';
import { decodeComputeReport } from './decoder';
import {
  COMPUTE_JOB_ID_PATTERN,
  COMPUTE_TASK_PATTERN,
  decodeComputeJobDetails,
  decodeComputeTaskJobs,
} from './detailDecoders';
import type { ComputeJobDetails, ComputeRange, ComputeReport, ComputeTaskJobs } from './types';

export async function fetchComputeReport(
  rangeHours: ComputeRange,
  signal?: AbortSignal,
): Promise<ComputeReport> {
  const payload = await getJson(`/api/compute?hours=${rangeHours}`, signal);
  return decodeComputeReport(payload);
}

export async function fetchComputeTaskJobs(
  rangeHours: ComputeRange,
  task: string,
  signal?: AbortSignal,
): Promise<ComputeTaskJobs> {
  if (!COMPUTE_TASK_PATTERN.test(task)) throw new TypeError('Invalid compute task');
  const payload = await getJson(
    `/api/compute/jobs?hours=${rangeHours}&task=${encodeURIComponent(task)}`,
    signal,
  );
  return decodeComputeTaskJobs(payload);
}

export async function fetchComputeJobDetails(
  jobId: string,
  signal?: AbortSignal,
): Promise<ComputeJobDetails> {
  if (!COMPUTE_JOB_ID_PATTERN.test(jobId)) throw new TypeError('Invalid compute job ID');
  const payload = await getJson(`/api/compute/jobs/${encodeURIComponent(jobId)}`, signal);
  return decodeComputeJobDetails(payload);
}
