import { describe, expect, it, vi } from 'vitest';

import {
  convergeUbntOperation,
  executeUbntMutation,
  UBNT_CONVERGENCE_INTERVAL_MS,
  UBNT_INITIAL_CONVERGENCE_DELAY_MS,
} from './controls';
import { sampleUbntStatus } from './testFixtures';

describe('UBNT operation orchestration', () => {
  it('waits 500ms for the first read, then 1.2s between running reads', async () => {
    const running = sampleUbntStatus('running');
    running.operation.kind = 'connect';
    const complete = sampleUbntStatus('complete');
    complete.operation.kind = 'connect';
    const refresh = vi.fn().mockResolvedValueOnce(running).mockResolvedValueOnce(complete);
    const pause = vi.fn().mockResolvedValue(undefined);

    const result = await convergeUbntOperation(refresh, pause);

    expect(result?.operation.status).toBe('complete');
    expect(pause).toHaveBeenNthCalledWith(1, UBNT_INITIAL_CONVERGENCE_DELAY_MS);
    expect(pause).toHaveBeenNthCalledWith(2, UBNT_CONVERGENCE_INTERVAL_MS);
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it('never retries a failed mutation and still performs one authoritative refresh', async () => {
    const mutation = vi.fn().mockRejectedValue(new Error('response lost'));
    const refresh = vi.fn().mockResolvedValue(sampleUbntStatus());
    const showToast = vi.fn();
    const pause = vi.fn().mockResolvedValue(undefined);

    const result = await executeUbntMutation(mutation, refresh, showToast, undefined, pause);

    expect(result).toBe(false);
    expect(mutation).toHaveBeenCalledOnce();
    expect(pause).not.toHaveBeenCalled();
    expect(refresh).toHaveBeenCalledOnce();
    expect(showToast).toHaveBeenCalledWith('response lost', 'error');
  });

  it('notifies the separate connectivity owner after a route-changing completion', async () => {
    const complete = sampleUbntStatus('complete');
    complete.operation.kind = 'provision';
    complete.operation.message = 'Network saved and connected';
    const mutation = vi.fn().mockResolvedValue({ message: 'started', status: complete });
    const refresh = vi.fn().mockResolvedValue(complete);
    const showToast = vi.fn();
    const connectivityChanged = vi.fn();
    const pause = vi.fn().mockResolvedValue(undefined);

    const result = await executeUbntMutation(
      mutation,
      refresh,
      showToast,
      connectivityChanged,
      pause,
    );

    expect(result).toBe(true);
    expect(connectivityChanged).toHaveBeenCalledOnce();
    expect(showToast).toHaveBeenCalledWith('Network saved and connected');
    expect(refresh).toHaveBeenCalledTimes(2); // convergence plus final reconciliation
  });

  it('lets an abort completion end the original mutation without a success toast', async () => {
    const started = sampleUbntStatus('running');
    started.operation.kind = 'connect';
    const aborted = sampleUbntStatus('complete');
    aborted.operation.kind = 'abort';
    aborted.operation.message = 'UBNT operation aborted; automatic selection resumed';
    const mutation = vi.fn().mockResolvedValue({ message: 'started', status: started });
    const refresh = vi.fn().mockResolvedValue(aborted);
    const showToast = vi.fn();
    const pause = vi.fn().mockResolvedValue(undefined);

    const result = await executeUbntMutation(mutation, refresh, showToast, undefined, pause);

    expect(result).toBe(false);
    expect(showToast).not.toHaveBeenCalled();
    expect(refresh).toHaveBeenCalledTimes(2);
  });
});
