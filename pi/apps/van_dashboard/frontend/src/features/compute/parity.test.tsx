import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { fetchComputeJobDetails, fetchComputeTaskJobs } from './api';
import { decodeComputeJobDetails, decodeComputeTaskJobs } from './detailDecoders';
import type { ComputeJobSummary } from './types';
import { useComputeExplorer } from './useComputeExplorer';

vi.mock('./api', () => ({ fetchComputeJobDetails: vi.fn(), fetchComputeTaskJobs: vi.fn() }));

const job: ComputeJobSummary = {
  id: '20260722T015900Z-deadbeef',
  task: 'repo-tests',
  state: 'failed',
  worker: 'm4mac',
  placement: 'remote',
  failureSummary: 'Task failed',
  submittedAt: 10,
  startedAt: 11,
  finishedAt: 14,
  queueSeconds: 1,
  activeSeconds: 3,
  cpuSeconds: 2,
  peakResidentBytes: 1024,
  inputBytes: 50,
  telemetryAvailable: true,
};

function rawJob() {
  return {
    id: job.id,
    task: job.task,
    state: job.state,
    worker: job.worker,
    placement: job.placement,
    failure_summary: job.failureSummary,
    submitted_at: 10,
    started_at: 11,
    finished_at: 14,
    queue_seconds: 1,
    active_seconds: 3,
    cpu_seconds: 2,
    peak_rss_bytes: 1024,
    input_bytes: 50,
    telemetry: true,
    exit_code: 1,
  };
}

function detailsPayload() {
  return {
    ok: true,
    job: rawJob(),
    diagnostics: {
      failure_classification: 'task',
      worker_error: null,
      resource_limit: null,
      resource_monitor_error: null,
      timed_out: false,
      interrupted: false,
      truncated: { worker_error: false, resource_limit: false, resource_monitor_error: false },
    },
    stderr: {
      available: true,
      bytes: 12,
      excerpt: 'failed test',
      truncated: false,
      excerpt_from: 'full',
    },
    stdout: { available: false, bytes: 0, excerpt: '', truncated: false, excerpt_from: 'none' },
  };
}

describe('compute parity decoders', () => {
  it('strictly decodes task jobs and retained diagnostics', () => {
    const tasks = decodeComputeTaskJobs({
      ok: true,
      range_hours: 168,
      task: 'repo-tests',
      matching_jobs: 1,
      truncated: false,
      jobs: [rawJob()],
    });
    const details = decodeComputeJobDetails(detailsPayload());
    expect(tasks.jobs[0]?.id).toBe(job.id);
    expect(details.stderr.excerpt).toBe('failed test');
    expect(details.exitCode).toBe(1);
  });

  it('rejects inconsistent task and unsafe job identities', () => {
    expect(() =>
      decodeComputeTaskJobs({
        ok: true,
        range_hours: 168,
        task: 'repo-tests',
        matching_jobs: 1,
        truncated: false,
        jobs: [{ ...rawJob(), task: 'other-task' }],
      }),
    ).toThrow(/another task/);
    expect(() =>
      decodeComputeJobDetails({ ...detailsPayload(), job: { ...rawJob(), id: '../bad' } }),
    ).toThrow(/invalid job/);
  });
});

describe('useComputeExplorer caches', () => {
  it('caches task lists and expanded details across collapse and rerender', async () => {
    vi.mocked(fetchComputeTaskJobs).mockResolvedValue({
      rangeHours: 168,
      task: 'repo-tests',
      matchingJobs: 1,
      truncated: false,
      jobs: [job],
    });
    vi.mocked(fetchComputeJobDetails).mockResolvedValue(decodeComputeJobDetails(detailsPayload()));
    const { result, rerender } = renderHook(({ jobs }) => useComputeExplorer(168, jobs), {
      initialProps: { jobs: [] as ComputeJobSummary[] },
    });

    act(() => result.current.toggleTask('repo-tests'));
    await waitFor(() => expect(result.current.jobs).toHaveLength(1));
    act(() => result.current.toggleTask('repo-tests'));
    act(() => result.current.toggleTask('repo-tests'));
    expect(fetchComputeTaskJobs).toHaveBeenCalledOnce();

    act(() => result.current.toggleDetails(job.id));
    await waitFor(() => expect(result.current.detail(job.id)).not.toBeNull());
    act(() => result.current.toggleDetails(job.id));
    rerender({ jobs: [job] });
    act(() => result.current.toggleDetails(job.id));
    expect(fetchComputeJobDetails).toHaveBeenCalledOnce();
    expect(result.current.expandedJobIds.has(job.id)).toBe(true);
  });
});
