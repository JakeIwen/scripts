import { act, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { fetchSystemHealthReport } from './api';
import { useSystemHealthReport } from './hooks';
import { sampleSystemHealth } from './testFixtures';
import type { SystemHealthRange } from './types';

vi.mock('./api', () => ({ fetchSystemHealthReport: vi.fn() }));

function Probe({ range }: { range: SystemHealthRange }) {
  const resource = useSystemHealthReport(range, false);
  return <output>{resource.data?.rangeHours ?? 'loading'}</output>;
}

describe('useSystemHealthReport', () => {
  it('reloads when the selected range changes', async () => {
    vi.mocked(fetchSystemHealthReport).mockImplementation(async (range) =>
      sampleSystemHealth(range),
    );
    const view = render(<Probe range={6} />);
    await act(async () => Promise.resolve());

    expect(fetchSystemHealthReport).toHaveBeenCalledWith(6, expect.any(AbortSignal));

    view.rerender(<Probe range={24} />);
    await act(async () => Promise.resolve());

    expect(fetchSystemHealthReport).toHaveBeenLastCalledWith(24, expect.any(AbortSignal));
  });
});
