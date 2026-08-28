import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '../../components/ToastProvider';
import type { PollingState } from '../../hooks/usePollingResource';
import { setCopAlert } from './api';
import { COP_CONVERGENCE_INTERVAL_MS, DashboardStatusTile } from './DashboardStatusTile';
import {
  decodeDashboardStatus,
  type CopCanWakeState,
  type DashboardStatus,
} from './dashboardStatus';

vi.mock('./api', () => ({ setCopAlert: vi.fn() }));

const setCopAlertMock = vi.mocked(setCopAlert);

function status(requested: boolean, state: CopCanWakeState): DashboardStatus {
  return decodeDashboardStatus({
    ok: true,
    cop_alert: {
      active: requested,
      ignition_on: false,
      ext_flood: requested ? 'on' : 'off',
      last_ntfy: null,
      last_error: null,
    },
    cop_can_wake: {
      available: true,
      service_active: true,
      state,
      marker_active: requested,
      last_detail: null,
      last_blocked_reason: null,
      last_blocked_detail: null,
      error: null,
    },
    cop_led: { phase: 'confirmed', message: 'Exterior state known', last_error: null },
    starlink: { state: 'off', available: true, changing: false, last_error: null },
    system_uptime: { seconds: 60, booted_at: null },
  });
}

function resource(
  data: DashboardStatus,
  refresh: PollingState<DashboardStatus>['refresh'],
): PollingState<DashboardStatus> {
  return {
    data,
    error: null,
    initialLoading: false,
    refreshing: false,
    lastUpdatedAt: Date.now(),
    refresh,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => {
    resolve = complete;
    reject = fail;
  });
  return { promise, resolve, reject };
}

describe('COP ALERT control', () => {
  beforeEach(() => setCopAlertMock.mockReset());
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('allows only one mutation while the first request is pending', async () => {
    vi.useFakeTimers();
    const pending = deferred<Awaited<ReturnType<typeof setCopAlert>>>();
    setCopAlertMock.mockReturnValue(pending.promise);
    const off = status(false, 'idle');
    const active = status(true, 'active_waiting');
    const refresh = vi.fn().mockResolvedValue(active);
    render(
      <ToastProvider>
        <DashboardStatusTile resource={resource(off, refresh)} />
      </ToastProvider>,
    );

    const arm = screen.getByRole('button', { name: 'Arm COP ALERT' });
    fireEvent.click(arm);
    fireEvent.click(arm);
    expect(setCopAlertMock).toHaveBeenCalledOnce();

    await act(async () => {
      pending.resolve({ message: 'COP ALERT armed', request: active.copAlert });
      await vi.advanceTimersByTimeAsync(COP_CONVERGENCE_INTERVAL_MS);
    });

    expect(refresh).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: 'Arm COP ALERT' })).toBeEnabled();
  });
});
