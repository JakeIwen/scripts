import { beforeEach, describe, expect, it, vi } from 'vitest';

import { getJson, postForm } from '../../api/client';
import { analyzePreviousCrash, fetchCrashHistory, fetchSystemHealthReport } from './api';
import { systemHealthPayload } from './testFixtures';

vi.mock('../../api/client', () => ({ getJson: vi.fn(), postForm: vi.fn() }));

describe('fetchSystemHealthReport', () => {
  beforeEach(() => {
    vi.mocked(getJson).mockResolvedValue(systemHealthPayload(168));
  });

  it('sends only the selected bounded range', async () => {
    const controller = new AbortController();
    const report = await fetchSystemHealthReport(168, controller.signal);

    expect(getJson).toHaveBeenCalledWith('/api/system-monitor?hours=168', controller.signal);
    expect(report.rangeHours).toBe(168);
  });

  it('uses the exact crash history path and empty analysis form', async () => {
    vi.mocked(getJson).mockResolvedValueOnce({ ok: true, history: [] });
    vi.mocked(postForm).mockResolvedValueOnce({
      ok: true,
      saved: false,
      comparison: null,
      analysis: {
        available: false,
        level: 'unknown',
        headline: 'No previous boot',
        findings: [],
        previous_boot: null,
        counts: {},
        timeline: [],
      },
    });
    const signal = new AbortController().signal;

    await fetchCrashHistory(signal);
    await analyzePreviousCrash();

    expect(getJson).toHaveBeenCalledWith('/api/system-monitor/crashes', signal);
    expect(postForm).toHaveBeenCalledWith('/api/system-monitor/crash-analysis');
  });
});
