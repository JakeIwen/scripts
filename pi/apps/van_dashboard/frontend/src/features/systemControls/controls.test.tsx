import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  fetchSystemPower,
  probeDashboard,
  requestDashboardRestart,
  requestSystemPower,
} from './api';
import { SYSTEM_CONTROL_POLL_INTERVAL_MS, useSystemControls } from './controls';

vi.mock('./api', () => ({
  fetchSystemPower: vi.fn(),
  probeDashboard: vi.fn(),
  requestDashboardRestart: vi.fn(),
  requestSystemPower: vi.fn(),
}));

const fetchPowerMock = vi.mocked(fetchSystemPower);
const probeMock = vi.mocked(probeDashboard);
const restartMock = vi.mocked(requestDashboardRestart);
const powerMock = vi.mocked(requestSystemPower);

const runningPower = {
  status: 'running' as const,
  action: 'reboot' as const,
  startedAt: 1_700_000_000,
  completedAt: null,
  error: null,
};

beforeEach(() => {
  vi.useFakeTimers();
  powerMock.mockResolvedValue({ message: 'reboot accepted', operation: runningPower });
  restartMock.mockResolvedValue({ message: 'restart scheduled', scheduledAt: 1_700_000_000 });
  fetchPowerMock.mockResolvedValue(runningPower);
  probeMock.mockResolvedValue(undefined);
});

afterEach(() => vi.useRealTimers());

describe('useSystemControls', () => {
  it('requires exact confirmation and prevents duplicate power requests', async () => {
    const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValue(true);
    let finish: ((value: { message: string; operation: typeof runningPower }) => void) | undefined;
    powerMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { result } = renderHook(() => useSystemControls(undefined, confirm));

    await act(async () => result.current.requestPower('reboot'));
    expect(powerMock).not.toHaveBeenCalled();

    let first: Promise<void> | undefined;
    let duplicate: Promise<void> | undefined;
    act(() => {
      first = result.current.requestPower('reboot');
      duplicate = result.current.requestPower('reboot');
    });
    expect(powerMock).toHaveBeenCalledTimes(1);
    act(() => finish?.({ message: 'reboot accepted', operation: runningPower }));
    fetchPowerMock.mockRejectedValueOnce(new TypeError('offline'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SYSTEM_CONTROL_POLL_INTERVAL_MS);
      await first;
      await duplicate;
    });
    expect(result.current.phase).toBe('power-disconnected');
    expect(result.current.locked).toBe(true);
  });

  it('treats observed restart disappearance followed by return as complete', async () => {
    const { result } = renderHook(() => useSystemControls(undefined, () => true));
    probeMock.mockRejectedValueOnce(new TypeError('offline')).mockResolvedValueOnce(undefined);
    let request: Promise<void> | undefined;
    act(() => {
      request = result.current.restartDashboard();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SYSTEM_CONTROL_POLL_INTERVAL_MS * 2);
      await request;
    });

    expect(restartMock).toHaveBeenCalledTimes(1);
    expect(probeMock).toHaveBeenCalledTimes(2);
    expect(result.current.phase).toBe('restart-complete');
    expect(result.current.locked).toBe(false);
  });
});
