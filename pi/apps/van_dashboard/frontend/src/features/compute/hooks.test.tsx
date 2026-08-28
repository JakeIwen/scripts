import { act, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { fetchComputeReport } from './api';
import { useComputeReport } from './hooks';
import { sampleComputeReport } from './testFixtures';
import type { ComputeRange } from './types';

vi.mock('./api', () => ({ fetchComputeReport: vi.fn() }));

function Probe({ range }: { range: ComputeRange }) {
  const resource = useComputeReport(range, false);
  return <output>{resource.data?.rangeHours ?? 'loading'}</output>;
}

describe('useComputeReport', () => {
  it('reloads when the selected range changes', async () => {
    vi.mocked(fetchComputeReport).mockImplementation(async (range) => sampleComputeReport(range));
    const view = render(<Probe range={168} />);
    await act(async () => Promise.resolve());

    expect(fetchComputeReport).toHaveBeenCalledWith(168, expect.any(AbortSignal));

    view.rerender(<Probe range={24} />);
    await act(async () => Promise.resolve());

    expect(fetchComputeReport).toHaveBeenLastCalledWith(24, expect.any(AbortSignal));
  });
});
