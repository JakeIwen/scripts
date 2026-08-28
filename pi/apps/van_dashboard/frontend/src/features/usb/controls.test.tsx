import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { discoverUsbPorts, recoverUsb2, startUsbPortAction } from './api';
import { useUsbControls } from './controls';
import { sampleUsbStatus } from './testFixtures';

vi.mock('./api', () => ({
  discoverUsbPorts: vi.fn(),
  recoverUsb2: vi.fn(),
  startUsbPortAction: vi.fn(),
}));

const discoverMock = vi.mocked(discoverUsbPorts);
const recoverMock = vi.mocked(recoverUsb2);
const portActionMock = vi.mocked(startUsbPortAction);

function runningUsbStatus() {
  const status = sampleUsbStatus();
  status.ports.operation = {
    status: 'running',
    key: '2-2:1',
    action: 'cycle',
    startedAt: 1_700_000_200,
    completedAt: null,
    message: null,
    error: null,
  };
  return status;
}

beforeEach(() => {
  const idle = sampleUsbStatus().ports;
  discoverMock.mockResolvedValue({ message: 'loaded', ports: idle });
  recoverMock.mockResolvedValue({ message: 'recovering', ports: runningUsbStatus().ports });
  portActionMock.mockResolvedValue({ message: 'cycling', ports: runningUsbStatus().ports });
});

describe('useUsbControls', () => {
  it('refuses to disconnect a port that reports mounted storage', async () => {
    const refresh = vi.fn().mockResolvedValue(sampleUsbStatus());
    const { result } = renderHook(() => useUsbControls(refresh));
    const port = sampleUsbStatus().ports.hubs[0]?.ports[0];
    if (!port) throw new Error('missing port fixture');
    port.mountedLabels = ['movingparts'];

    await act(async () => result.current.runPortAction(port, 'cycle'));
    expect(portActionMock).not.toHaveBeenCalled();
    expect(result.current.lastError).toContain('Unmount movingparts');
  });

  it('confirms whole USB-2 recovery before sending it', async () => {
    const confirm = vi.fn().mockReturnValue(false);
    const { result } = renderHook(() =>
      useUsbControls(vi.fn().mockResolvedValue(sampleUsbStatus()), undefined, confirm),
    );
    await act(async () => result.current.recoverUsb2());
    expect(confirm).toHaveBeenCalledOnce();
    expect(recoverMock).not.toHaveBeenCalled();
  });

  it('refreshes after ambiguous failure without retrying', async () => {
    portActionMock.mockRejectedValueOnce(new Error('connection reset'));
    const refresh = vi.fn().mockResolvedValue(sampleUsbStatus());
    const { result } = renderHook(() => useUsbControls(refresh));
    const port = sampleUsbStatus().ports.hubs[0]?.ports[0];
    if (!port) throw new Error('missing port fixture');

    await act(async () => result.current.runPortAction(port, 'off'));
    expect(portActionMock).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(result.current.uncertainOutcome).toBe(false);
  });

  it('keeps accepted asynchronous work blocked until a matching GET', async () => {
    const { result } = renderHook(() => useUsbControls(vi.fn().mockResolvedValue(null)));
    const port = sampleUsbStatus().ports.hubs[0]?.ports[0];
    if (!port) throw new Error('missing port fixture');

    await act(async () => result.current.runPortAction(port, 'cycle'));
    expect(result.current.pendingOperation).not.toBeNull();
    expect(result.current.blocked).toBe(true);

    act(() => result.current.reconcile(runningUsbStatus()));
    expect(result.current.pendingOperation).toBeNull();
  });
});
