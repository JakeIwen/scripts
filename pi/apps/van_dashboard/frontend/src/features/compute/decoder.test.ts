import { describe, expect, it } from 'vitest';

import { decodeComputeReport } from './decoder';
import { formatComputeDuration } from './presentation';
import { projectComputeJobs } from './projections';
import { computePayload } from './testFixtures';

describe('compute projections', () => {
  it('formats subsecond work without displaying a rounded 1000 ms edge case', () => {
    expect(formatComputeDuration(0.331)).toBe('331 ms');
    expect(formatComputeDuration(0.9998)).toBe('1.00 s');
  });

  it('projects outer status, totals, task rollups, and recent jobs', () => {
    const report = decodeComputeReport(computePayload(24));

    expect(report.rangeHours).toBe(24);
    expect(report.status.workers[0]).toEqual({
      name: 'm4mac',
      placement: 'remote',
      available: true,
      seenAt: 1_774_999_995,
      ageSeconds: 5,
      slotsTotal: 10,
      slotsBusy: 3,
      slotsAvailable: 7,
    });
    expect(report.summary.macCpuSeconds).toBe(6.5);
    expect(report.tasks[0]?.task).toBe('repo-tests');
    expect(report.jobs[1]?.failureSummary).toBe('Task exited with code 1');
    expect(report.eligibleLocalWork.reasons).toEqual([
      { reason: 'worker-unavailable', events: 1 },
      { reason: 'unsupported', events: 1 },
    ]);
  });

  it('drops malformed producer-owned jobs without exposing their nested fields', () => {
    const jobs = projectComputeJobs([
      {
        id: 'good',
        task: 'repo-tests',
        state: 'done',
        placement: 'remote',
        telemetry: true,
        diagnostics: { raw_output: 'must not escape' },
      },
      { id: 'future', task: 'repo-tests', state: 'new-future-state' },
      'malformed',
    ]);

    expect(jobs).toHaveLength(1);
    expect(jobs[0]).not.toHaveProperty('diagnostics');
  });

  it('rejects malformed stable scheduler counts', () => {
    const payload = computePayload();
    payload.status.queued = -1;
    expect(() => decodeComputeReport(payload)).toThrow(
      'compute status.queued must be a non-negative integer',
    );
  });

  it('supports the bounded route contract before optional telemetry fields exist', () => {
    const report = decodeComputeReport({
      ok: true,
      range_hours: 6,
      status: { available: true, queued: 0, running: 0 },
      summary: { jobs: 2, mac_cpu_seconds: 3.5 },
      tasks: [],
      jobs: [],
    });

    expect(report.status.localRunning).toBe(0);
    expect(report.summary.macCpuSeconds).toBe(3.5);
    expect(report.eligibleLocalWork.events).toBe(0);
  });
});
