import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchUbntWifiStatus } from './api';
import { UBNT_OPERATION_INTERVAL_MS, useUbntWifiStatus } from './hooks';
import { sampleUbntStatus } from './testFixtures';

vi.mock('./api', () => ({ fetchUbntWifiStatus: vi.fn() }));

function Probe() {
  const resource = useUbntWifiStatus();
  return <output>{resource.data?.operation.status ?? 'loading'}</output>;
}

describe('useUbntWifiStatus', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('switches to the audited operation cadence while an operation is running', async () => {
    vi.mocked(fetchUbntWifiStatus).mockResolvedValue(sampleUbntStatus('running'));
    render(<Probe />);
    await act(async () => Promise.resolve());
    await act(async () => Promise.resolve());
    const callsAfterIntervalChange = vi.mocked(fetchUbntWifiStatus).mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(UBNT_OPERATION_INTERVAL_MS - 1);
    });
    expect(fetchUbntWifiStatus).toHaveBeenCalledTimes(callsAfterIntervalChange);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fetchUbntWifiStatus).toHaveBeenCalledTimes(callsAfterIntervalChange + 1);
  });
});
