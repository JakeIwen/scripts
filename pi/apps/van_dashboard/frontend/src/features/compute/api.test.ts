import { beforeEach, describe, expect, it, vi } from 'vitest';

import { getJson } from '../../api/client';
import { fetchComputeReport } from './api';
import { computePayload } from './testFixtures';

vi.mock('../../api/client', () => ({ getJson: vi.fn() }));

describe('fetchComputeReport', () => {
  beforeEach(() => {
    vi.mocked(getJson).mockResolvedValue(computePayload(720));
  });

  it('sends only the selected bounded range', async () => {
    const controller = new AbortController();
    const report = await fetchComputeReport(720, controller.signal);

    expect(getJson).toHaveBeenCalledWith('/api/compute?hours=720', controller.signal);
    expect(report.rangeHours).toBe(720);
  });
});
