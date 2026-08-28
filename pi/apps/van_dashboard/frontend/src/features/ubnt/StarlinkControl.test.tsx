import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '../../components/ToastProvider';
import { toggleStarlinkPower } from '../status/api';
import type { DashboardStatus, StarlinkStatus } from '../status/dashboardStatus';
import { StarlinkControl, type StarlinkStatusResource } from './StarlinkControl';

vi.mock('../status/api', () => ({ toggleStarlinkPower: vi.fn() }));

function dashboardStatus(starlink: StarlinkStatus): DashboardStatus {
  return {
    copAlert: {
      requested: false,
      ignitionOn: false,
      exteriorFloodState: 'off',
      lastNotificationAt: null,
      lastError: null,
    },
    copExecution: {
      available: true,
      serviceActive: true,
      state: 'idle',
      markerActive: false,
      lastDetail: null,
      lastBlockedReason: null,
      lastBlockedDetail: null,
      error: null,
    },
    copLed: { phase: 'inactive', message: 'COP ALERT is off', lastError: null },
    starlink,
    systemUptime: { seconds: 100, bootedAt: null },
  };
}

function starlinkStatus(state: 'on' | 'off' | 'unknown' = 'off'): StarlinkStatus {
  return {
    state,
    available: state !== 'unknown',
    changing: false,
    lastError: null,
  };
}

function resource(status = starlinkStatus()): StarlinkStatusResource {
  const data = dashboardStatus(status);
  return {
    data,
    error: null,
    refreshing: false,
    refresh: vi.fn(async () => data),
  };
}

function renderControl(starlinkResource: StarlinkStatusResource) {
  return render(
    <ToastProvider>
      <StarlinkControl resource={starlinkResource} />
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.mocked(toggleStarlinkPower).mockResolvedValue({
    message: 'Starlink power on',
    status: starlinkStatus('on'),
  });
});

afterEach(cleanup);

describe('StarlinkControl', () => {
  it('disables power changes when the status is unknown', () => {
    renderControl(resource(starlinkStatus('unknown')));

    expect(screen.getByRole('button', { name: 'Turn Starlink on' })).toBeDisabled();
    expect(screen.getByText('No data')).toBeInTheDocument();
  });

  it('confirms, toggles once, and refreshes authoritative status', async () => {
    const statusResource = resource();
    const confirm = vi
      .spyOn(window, 'confirm')
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    renderControl(statusResource);
    const button = screen.getByRole('button', { name: 'Turn Starlink on' });

    fireEvent.click(button);
    expect(toggleStarlinkPower).not.toHaveBeenCalled();
    expect(statusResource.refresh).not.toHaveBeenCalled();

    fireEvent.click(button);
    await waitFor(() => expect(statusResource.refresh).toHaveBeenCalledTimes(2));
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('Turn Starlink power on?'));
    expect(toggleStarlinkPower).toHaveBeenCalledOnce();
    expect(screen.getByText('Starlink power on')).toBeInTheDocument();
  });

  it('does not retry a failed toggle and still refreshes status', async () => {
    const statusResource = resource(starlinkStatus('on'));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.mocked(toggleStarlinkPower).mockRejectedValueOnce(new Error('Tuya switch unavailable'));
    renderControl(statusResource);

    fireEvent.click(screen.getByRole('button', { name: 'Turn Starlink off' }));
    await waitFor(() => expect(statusResource.refresh).toHaveBeenCalledTimes(2));
    expect(toggleStarlinkPower).toHaveBeenCalledOnce();
    expect(screen.getByText('Tuya switch unavailable')).toBeInTheDocument();
  });

  it('keeps the toggle single-flight', async () => {
    let release: ((value: { message: string; status: StarlinkStatus }) => void) | undefined;
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.mocked(toggleStarlinkPower).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const statusResource = resource();
    renderControl(statusResource);
    const button = screen.getByRole('button', { name: 'Turn Starlink on' });

    fireEvent.click(button);
    fireEvent.click(button);
    expect(toggleStarlinkPower).toHaveBeenCalledOnce();

    release?.({ message: 'Starlink power on', status: starlinkStatus('on') });
    await waitFor(() => expect(statusResource.refresh).toHaveBeenCalledTimes(2));
    expect(toggleStarlinkPower).toHaveBeenCalledOnce();
  });
});
