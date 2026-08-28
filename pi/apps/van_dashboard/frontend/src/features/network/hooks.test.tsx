import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchActiveConnectivity, fetchOpenWrtClients, fetchSpeedtestStatus } from './api';
import {
  ACTIVE_CONNECTIVITY_INTERVAL_MS,
  OPENWRT_CLIENT_INTERVAL_MS,
  RUNNING_SPEEDTEST_INTERVAL_MS,
  useActiveConnectivity,
  useOpenWrtClients,
  useSpeedtestStatus,
} from './hooks';
import { sampleClients, sampleConnectivity, sampleSpeedtest } from './testFixtures';

vi.mock('./api', () => ({
  fetchActiveConnectivity: vi.fn(),
  fetchOpenWrtClients: vi.fn(),
  fetchSpeedtestStatus: vi.fn(),
}));

const activeConnectivityMock = vi.mocked(fetchActiveConnectivity);
const openWrtClientsMock = vi.mocked(fetchOpenWrtClients);
const speedtestStatusMock = vi.mocked(fetchSpeedtestStatus);

function ConnectivityProbe() {
  const resource = useActiveConnectivity();
  return <output>{resource.data?.router.mode ?? 'loading'}</output>;
}

function ClientsProbe({ open }: { open: boolean }) {
  const resource = useOpenWrtClients(open);
  return <output>{resource.data?.clientCount ?? 'closed'}</output>;
}

function SpeedtestProbe() {
  const resource = useSpeedtestStatus();
  return <output>{resource.data?.status ?? 'loading'}</output>;
}

describe('network polling hooks', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    openWrtClientsMock.mockResolvedValue(sampleClients());
    speedtestStatusMock.mockResolvedValue(sampleSpeedtest());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('waits for the active connectivity request to settle before scheduling another', async () => {
    let resolveFirst: ((value: ReturnType<typeof sampleConnectivity>) => void) | undefined;
    activeConnectivityMock
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockResolvedValue(sampleConnectivity());

    render(<ConnectivityProbe />);
    await act(async () => Promise.resolve());
    expect(activeConnectivityMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(ACTIVE_CONNECTIVITY_INTERVAL_MS * 5);
    });
    expect(activeConnectivityMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFirst?.(sampleConnectivity());
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ACTIVE_CONNECTIVITY_INTERVAL_MS);
    });
    expect(activeConnectivityMock).toHaveBeenCalledTimes(2);
  });

  it('does not request the client inventory until its sheet is open', async () => {
    activeConnectivityMock.mockResolvedValue(sampleConnectivity());
    const view = render(<ClientsProbe open={false} />);
    await act(async () => Promise.resolve());
    expect(openWrtClientsMock).not.toHaveBeenCalled();

    view.rerender(<ClientsProbe open />);
    await act(async () => Promise.resolve());
    expect(openWrtClientsMock).toHaveBeenCalledTimes(1);

    view.rerender(<ClientsProbe open={false} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(OPENWRT_CLIENT_INTERVAL_MS * 2);
    });
    expect(openWrtClientsMock).toHaveBeenCalledTimes(1);
  });

  it('polls once a second while a speed test is running', async () => {
    activeConnectivityMock.mockResolvedValue(sampleConnectivity());
    speedtestStatusMock.mockResolvedValue(sampleSpeedtest('running'));

    render(<SpeedtestProbe />);
    await act(async () => Promise.resolve());
    const callsAfterInitialStatus = speedtestStatusMock.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUNNING_SPEEDTEST_INTERVAL_MS);
    });

    expect(speedtestStatusMock.mock.calls.length).toBeGreaterThan(callsAfterInitialStatus);
  });
});
