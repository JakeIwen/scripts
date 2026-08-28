import { describe, expect, it, vi } from 'vitest';

import type { PollingState } from '../../hooks/usePollingResource';
import {
  convergeCopStatus,
  copConverged,
  COP_CONVERGENCE_INTERVAL_MS,
  executeCopAlertChange,
} from './DashboardStatusTile';
import {
  decodeDashboardStatus,
  type CopCanWakeState,
  type DashboardStatus,
} from './dashboardStatus';

function status(requested: boolean, state: CopCanWakeState, available = true): DashboardStatus {
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
      available,
      service_active: available,
      state,
      marker_active: requested,
      last_detail: null,
      last_blocked_reason: null,
      last_blocked_detail: null,
      error: available ? null : 'supervisor unavailable',
    },
    cop_led: { phase: 'confirmed', message: 'Exterior state known', last_error: null },
    starlink: { state: 'off', available: true, changing: false, last_error: null },
    system_uptime: { seconds: 60, booted_at: null },
  });
}

describe('COP status convergence', () => {
  it('requires both the request bit and supervisor state to converge', () => {
    expect(copConverged(status(false, 'active_waiting'), true)).toBe(false);
    expect(copConverged(status(true, 'active_waiting'), true)).toBe(true);
    expect(copConverged(status(false, 'idle'), false)).toBe(true);
    expect(copConverged(status(true, 'idle'), false)).toBe(false);
  });

  it('matches legacy terminal handling for unavailable and stopping supervisors', () => {
    expect(copConverged(status(true, 'stopping'), true)).toBe(true);
    expect(copConverged(status(true, 'stopped', false), true)).toBe(true);
  });

  it('polls at 500ms until an authoritative terminal snapshot arrives', async () => {
    const arming = status(true, 'arming_delay');
    const active = status(true, 'active_waiting');
    const refresh = vi
      .fn<PollingState<DashboardStatus>['refresh']>()
      .mockResolvedValueOnce(arming)
      .mockResolvedValueOnce(active);
    const pause = vi.fn().mockResolvedValue(undefined);

    const result = await convergeCopStatus(refresh, true, pause);

    expect(result).toBe(active);
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(pause).toHaveBeenNthCalledWith(1, COP_CONVERGENCE_INTERVAL_MS);
    expect(pause).toHaveBeenNthCalledWith(2, COP_CONVERGENCE_INTERVAL_MS);
  });

  it('never retries a failed mutation and performs an authoritative refresh', async () => {
    const mutation = vi.fn().mockRejectedValue(new Error('control response lost'));
    const refresh = vi.fn().mockResolvedValue(status(false, 'idle'));
    const setOverride = vi.fn();
    const showToast = vi.fn();
    const pause = vi.fn().mockResolvedValue(undefined);

    const result = await executeCopAlertChange(
      true,
      mutation,
      refresh,
      setOverride,
      showToast,
      pause,
    );

    expect(result).toBe(false);
    expect(mutation).toHaveBeenCalledOnce();
    expect(pause).not.toHaveBeenCalled();
    expect(refresh).toHaveBeenCalledOnce();
    expect(setOverride).toHaveBeenCalledOnce();
    expect(setOverride).toHaveBeenCalledWith(null);
    expect(showToast).toHaveBeenCalledWith('control response lost', 'error');
  });
});
