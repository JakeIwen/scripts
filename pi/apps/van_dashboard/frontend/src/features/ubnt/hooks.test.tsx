import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchUbntWifiStatus } from './api';
import { UBNT_IDLE_INTERVAL_MS, UBNT_OPERATION_INTERVAL_MS, useUbntWifiStatus } from './hooks';
import { decodeUbntWifiStatus } from './decoder';
import { ubntPayload } from './testFixtures';
import { sampleUbntStatus } from './testFixtures';

vi.mock('./api', () => ({ fetchUbntWifiStatus: vi.fn() }));

function Probe() {
  const resource = useUbntWifiStatus();
  return <output>{resource.data?.state.associatedSsid ?? 'loading'}</output>;
}

describe('useUbntWifiStatus', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    cleanup();
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

  it('collects a background status result promptly after the operation has ended', async () => {
    const cached = {
      ...ubntPayload('error'),
      refreshing: true,
      last_error: 'UBNT status interrupted: Connection refused',
    };
    cached.wifi.state.configured_ssid = 'vanpi-disconnected-27615';
    cached.wifi.state.associated_ssid = '';
    cached.wifi.state.ccq_percent = 0;
    vi.mocked(fetchUbntWifiStatus).mockResolvedValue(decodeUbntWifiStatus(cached));
    render(<Probe />);
    await act(async () => Promise.resolve());

    const fresh = { ...ubntPayload('error'), refreshing: false, last_error: null };
    fresh.wifi.state.selector_running = false;
    fresh.wifi.state.associated_ssid = "Admirals' Club!";
    fresh.wifi.state.configured_ssid = "Admirals' Club!";
    vi.mocked(fetchUbntWifiStatus).mockResolvedValue(decodeUbntWifiStatus(fresh));
    await act(async () => vi.advanceTimersByTimeAsync(UBNT_OPERATION_INTERVAL_MS));
    expect(screen.getByRole('status')).toHaveTextContent("Admirals' Club!");

    const calls = vi.mocked(fetchUbntWifiStatus).mock.calls.length;
    await act(async () => vi.advanceTimersByTimeAsync(UBNT_IDLE_INTERVAL_MS - 1));
    expect(fetchUbntWifiStatus).toHaveBeenCalledTimes(calls);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(fetchUbntWifiStatus).toHaveBeenCalledTimes(calls + 1);
  });

  it.each(['read', 'selector', 'queued power change', 'failed read'])(
    'keeps polling promptly for a pending %s after an operation ends',
    async (pending) => {
      const status = sampleUbntStatus('error');
      status.state.selectorRunning = pending === 'selector';
      status.statusRefreshing = pending === 'read';
      status.starlinkPending = pending === 'queued power change';
      status.lastError = pending === 'failed read' ? 'Connection refused' : null;
      vi.mocked(fetchUbntWifiStatus).mockResolvedValue(status);
      render(<Probe />);
      await act(async () => Promise.resolve());
      const calls = vi.mocked(fetchUbntWifiStatus).mock.calls.length;
      await act(async () => vi.advanceTimersByTimeAsync(UBNT_OPERATION_INTERVAL_MS));
      expect(fetchUbntWifiStatus).toHaveBeenCalledTimes(calls + 1);
    },
  );
});
