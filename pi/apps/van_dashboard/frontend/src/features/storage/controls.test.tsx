import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { startDiskAction, updateStoragePolicy } from './api';
import { useStorageControls } from './controls';
import { sampleDiskStatus, sampleStoragePolicy } from './testFixtures';

vi.mock('./api', () => ({
  startDiskAction: vi.fn(),
  updateStoragePolicy: vi.fn(),
}));

const diskActionMock = vi.mocked(startDiskAction);
const policyMock = vi.mocked(updateStoragePolicy);

function runningDiskStatus() {
  const status = sampleDiskStatus();
  status.operation = {
    status: 'running',
    action: 'eject',
    label: 'movingparts',
    startedAt: 1_700_000_200,
    completedAt: null,
    error: null,
  };
  return status;
}

beforeEach(() => {
  policyMock.mockResolvedValue({ message: 'policy updated', policy: sampleStoragePolicy() });
  diskActionMock.mockResolvedValue({
    message: 'disk action started',
    diskStatus: runningDiskStatus(),
  });
});

describe('useStorageControls', () => {
  it('requires confirmation for eject and repair but not mount', async () => {
    const confirm = vi.fn().mockReturnValue(false);
    const refreshPolicy = vi.fn().mockResolvedValue(sampleStoragePolicy());
    const refreshDisks = vi.fn().mockResolvedValue(sampleDiskStatus());
    const { result } = renderHook(() =>
      useStorageControls(refreshPolicy, refreshDisks, undefined, confirm),
    );
    const mounted = sampleDiskStatus().disks[0];
    if (!mounted) throw new Error('missing disk fixture');

    await act(async () => result.current.runDiskAction(mounted, 'eject'));
    await act(async () => result.current.runDiskAction(mounted, 'repair'));
    expect(diskActionMock).not.toHaveBeenCalled();
    expect(confirm).toHaveBeenCalledTimes(2);

    const unmounted = { ...mounted, mounted: false, mountpoints: [] };
    await act(async () => result.current.runDiskAction(unmounted, 'mount'));
    expect(diskActionMock).toHaveBeenCalledWith('movingparts', 'mount');
  });

  it('prevents duplicate desired-state mutations', async () => {
    let finish:
      | ((value: { message: string; policy: ReturnType<typeof sampleStoragePolicy> }) => void)
      | undefined;
    policyMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const refreshPolicy = vi.fn().mockResolvedValue(sampleStoragePolicy());
    const { result } = renderHook(() =>
      useStorageControls(refreshPolicy, vi.fn().mockResolvedValue(sampleDiskStatus())),
    );
    let first: Promise<void> | undefined;
    let duplicate: Promise<void> | undefined;

    act(() => {
      first = result.current.setPolicy('disks_enabled', false);
      duplicate = result.current.setPolicy('disks_enabled', false);
    });
    expect(policyMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      finish?.({ message: 'policy updated', policy: sampleStoragePolicy() });
      await first;
      await duplicate;
    });
    expect(refreshPolicy).toHaveBeenCalledTimes(1);
  });

  it('refreshes after failure without retrying and fails closed if refresh fails', async () => {
    policyMock.mockRejectedValueOnce(new Error('connection lost'));
    const refreshPolicy = vi.fn().mockResolvedValue(null);
    const { result } = renderHook(() =>
      useStorageControls(refreshPolicy, vi.fn().mockResolvedValue(sampleDiskStatus())),
    );

    await act(async () => result.current.setPolicy('torrents_enabled', false));

    expect(policyMock).toHaveBeenCalledTimes(1);
    expect(refreshPolicy).toHaveBeenCalledTimes(1);
    expect(result.current.uncertainOutcome).toBe(true);
    expect(result.current.blocked).toBe(true);
  });

  it('keeps an accepted disk action blocked until authoritative observation', async () => {
    const refreshDisks = vi.fn().mockResolvedValue(null);
    const { result } = renderHook(() =>
      useStorageControls(vi.fn().mockResolvedValue(sampleStoragePolicy()), refreshDisks),
    );
    const disk = sampleDiskStatus().disks[0];
    if (!disk) throw new Error('missing disk fixture');

    await act(async () => result.current.runDiskAction(disk, 'mount'));
    expect(result.current.pendingDiskOperation).not.toBeNull();
    expect(result.current.blocked).toBe(true);

    act(() => result.current.reconcileDiskStatus(runningDiskStatus()));
    expect(result.current.pendingDiskOperation).toBeNull();
  });
});
