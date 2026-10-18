import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../components/ToastProvider';
import type { PollingState } from '../../hooks/usePollingResource';
import { DashboardStatusTile } from './DashboardStatusTile';
import { decodeDashboardStatus, type DashboardStatus } from './dashboardStatus';

vi.mock('../../api/client', () => ({ getJson: vi.fn() }));

const statusPayload = {
  ok: true,
  cop_alert: {
    active: true,
    ignition_on: false,
    ext_flood: 'on',
    last_ntfy: 1_700_000_000,
    last_error: null,
  },
  cop_can_wake: {
    available: true,
    service_active: true,
    state: 'blocked',
    marker_active: true,
    last_detail: 'Waiting for the accessory network',
    last_blocked_reason: 'Bus quiet',
    last_blocked_detail: 'No wake response',
    error: null,
  },
  cop_led: {
    phase: 'confirmed',
    message: 'Exterior LED is on',
    last_error: null,
  },
  starlink: {
    entity: 'switch.starlink',
    state: 'off',
    available: true,
    checked_at: 1_700_000_000,
    last_error: null,
    refreshing: false,
    changing: false,
  },
  system_uptime: {
    seconds: 183_840,
    booted_at: '2026-08-24T12:00:00+00:00',
  },
};

function resource(data: DashboardStatus): PollingState<DashboardStatus> {
  return {
    data,
    error: null,
    initialLoading: false,
    refreshing: false,
    lastUpdatedAt: Date.now(),
    refresh: vi.fn(async () => data),
  };
}

describe('decodeDashboardStatus', () => {
  it('gives the API fields readable frontend names', () => {
    const status = decodeDashboardStatus(statusPayload);

    expect(status.copAlert.requested).toBe(true);
    expect(status.copExecution.state).toBe('blocked');
    expect(status.copExecution.lastBlockedDetail).toBe('No wake response');
    expect(status.copLed.message).toBe('Exterior LED is on');
    expect(status.systemUptime.seconds).toBe(183_840);
  });

  it('accepts the intentionally small unavailable CAN-wake response', () => {
    const status = decodeDashboardStatus({
      ...statusPayload,
      cop_can_wake: {
        available: false,
        service_active: false,
        error: 'CAN wake supervisor is not active',
      },
    });

    expect(status.copExecution.state).toBeNull();
    expect(status.copExecution.error).toBe('CAN wake supervisor is not active');
  });
});

describe('DashboardStatusTile', () => {
  it('shows request and execution separately with an explicit intent control', () => {
    render(
      <ToastProvider>
        <DashboardStatusTile resource={resource(decodeDashboardStatus(statusPayload))} />
      </ToastProvider>,
    );

    expect(screen.getByText('Requested')).toBeInTheDocument();
    expect(screen.getByText('Blocked')).toBeInTheDocument();
    expect(screen.getByText('No wake response')).toBeInTheDocument();
    expect(screen.getByText('Exterior LED is on')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Disarm STRANGER DANGER' })).toBeEnabled();
  });
});
