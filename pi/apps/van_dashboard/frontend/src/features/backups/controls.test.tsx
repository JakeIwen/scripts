import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { setCloneCardNominalGb, startBackup, startBackupClone, stopBackup } from './api';
import { useBackupControls } from './controls';
import { sampleBackupStatus } from './testFixtures';

vi.mock('./api', () => ({
  startBackup: vi.fn(),
  startBackupClone: vi.fn(),
  stopBackup: vi.fn(),
  setCloneCardNominalGb: vi.fn(),
}));

const startMock = vi.mocked(startBackup);
const cloneMock = vi.mocked(startBackupClone);
const stopMock = vi.mocked(stopBackup);
const capacityMock = vi.mocked(setCloneCardNominalGb);

beforeEach(() => {
  startMock.mockResolvedValue({ message: 'started', backups: sampleBackupStatus(true) });
  cloneMock.mockResolvedValue({ message: 'cloning', backups: sampleBackupStatus(true) });
  stopMock.mockResolvedValue({ message: 'stopping', backups: sampleBackupStatus(true) });
  capacityMock.mockResolvedValue({ message: 'capacity updated', backups: sampleBackupStatus() });
});

describe('useBackupControls', () => {
  it('requires confirmation for start, stop, and clone', async () => {
    const confirm = vi.fn().mockReturnValue(false);
    const status = sampleBackupStatus(true);
    const target = status.hotswaps[0];
    if (!target) throw new Error('missing hotspare fixture');
    const { result } = renderHook(() =>
      useBackupControls(vi.fn().mockResolvedValue(status), undefined, confirm),
    );

    await act(async () => result.current.start('borg'));
    await act(async () => result.current.stop('borg', status));
    await act(async () => result.current.clone(target));
    expect(confirm).toHaveBeenCalledTimes(3);
    expect(startMock).not.toHaveBeenCalled();
    expect(stopMock).not.toHaveBeenCalled();
    expect(cloneMock).not.toHaveBeenCalled();
  });

  it('refreshes after failure and does not retry', async () => {
    startMock.mockRejectedValueOnce(new Error('backup response lost'));
    const refresh = vi.fn().mockResolvedValue(sampleBackupStatus());
    const { result } = renderHook(() => useBackupControls(refresh, undefined, () => true));

    await act(async () => result.current.start('exfat'));
    expect(startMock).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(result.current.uncertainOutcome).toBe(false);
  });

  it('updates clone-card capacity without a confirmation prompt', async () => {
    const confirm = vi.fn().mockReturnValue(false);
    const refresh = vi.fn().mockResolvedValue(sampleBackupStatus());
    const { result } = renderHook(() => useBackupControls(refresh, undefined, confirm));

    await act(async () => result.current.setCloneCardNominalGb(128));

    expect(capacityMock).toHaveBeenCalledWith(128);
    expect(confirm).not.toHaveBeenCalled();
    expect(refresh).toHaveBeenCalledOnce();
  });

  it('keeps accepted work blocked until matching polled state is observed', async () => {
    const running = sampleBackupStatus(true);
    const { result } = renderHook(() =>
      useBackupControls(vi.fn().mockResolvedValue(null), undefined, () => true),
    );

    await act(async () => result.current.start('borg'));
    expect(result.current.pendingOperation).not.toBeNull();
    expect(result.current.blocked).toBe(true);

    act(() => result.current.reconcile(running));
    expect(result.current.pendingOperation).toBeNull();
  });

  it('includes the in-progress clone warning when stopping Borg', async () => {
    const status = sampleBackupStatus(true);
    if (!status.borg.progress) throw new Error('missing Borg progress fixture');
    status.borg.progress.phase = 'cloning';
    const confirm = vi.fn().mockReturnValue(false);
    const { result } = renderHook(() =>
      useBackupControls(vi.fn().mockResolvedValue(status), undefined, confirm),
    );

    await act(async () => result.current.stop('borg', status));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('hotspare'));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('scheduled run may retry'));
  });

  it('announces a polled graceful-stop completion only once', () => {
    const notify = vi.fn();
    const { result } = renderHook(() =>
      useBackupControls(vi.fn().mockResolvedValue(null), notify, () => true),
    );
    const stopping = sampleBackupStatus(true);
    stopping.stop = {
      status: 'running',
      kind: 'exfat',
      startedAt: 1_700_000_110,
      completedAt: null,
      error: null,
    };
    const complete = sampleBackupStatus(false);
    complete.stop = {
      status: 'complete',
      kind: 'exfat',
      startedAt: 1_700_000_110,
      completedAt: 1_700_000_120,
      error: null,
    };

    act(() => result.current.reconcile(stopping));
    act(() => result.current.reconcile(complete));
    act(() => result.current.reconcile(complete));

    expect(notify).toHaveBeenCalledOnce();
    expect(notify).toHaveBeenCalledWith('EXFAT512 backup stopped', 'normal');
    expect(result.current.lastMessage).toBe('EXFAT512 backup stopped');
  });
});
