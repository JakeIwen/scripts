import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '../../components/ToastProvider';
import type { PollingState } from '../../hooks/usePollingResource';
import { scanUbntNetworks } from './api';
import { UBNT_INITIAL_CONVERGENCE_DELAY_MS, useUbntControls } from './controls';
import { sampleUbntStatus } from './testFixtures';
import type { UbntMutationResult, UbntWifiStatus } from './types';

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  scanUbntNetworks: vi.fn(),
}));

const scanMock = vi.mocked(scanUbntNetworks);

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

describe('useUbntControls', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    scanMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('starts only one mutation when the action is triggered twice', async () => {
    const pending = deferred<UbntMutationResult>();
    scanMock.mockReturnValue(pending.promise);
    const complete = sampleUbntStatus('complete');
    complete.operation.kind = 'scan';
    const resource: PollingState<UbntWifiStatus> = {
      data: complete,
      error: null,
      initialLoading: false,
      refreshing: false,
      lastUpdatedAt: Date.now(),
      refresh: vi.fn().mockResolvedValue(complete),
    };
    const { result } = renderHook(() => useUbntControls(resource), {
      wrapper: ToastProvider,
    });

    let first!: Promise<boolean>;
    let duplicate!: Promise<boolean>;
    act(() => {
      first = result.current.scan();
      duplicate = result.current.scan();
    });
    expect(scanMock).toHaveBeenCalledOnce();

    const running = sampleUbntStatus('running');
    running.operation.kind = 'scan';
    await act(async () => {
      pending.resolve({ message: 'UBNT scan started', status: running });
      await vi.advanceTimersByTimeAsync(UBNT_INITIAL_CONVERGENCE_DELAY_MS);
      await first;
    });

    await expect(duplicate).resolves.toBe(false);
    expect(resource.refresh).toHaveBeenCalledTimes(2);
  });
});
