import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { getJson, postForm } from '../../api/client';
import { formatIgnitionDuration, ignitionRemainingSeconds } from './countdown';
import { decodeIgnitionMonitorStatus } from './decoders';
import {
  IGNITION_SHEET_POLL_INTERVAL_MS,
  IGNITION_TILE_POLL_INTERVAL_MS,
  useIgnitionMonitorStatus,
} from './hooks';
import { IgnitionSheet } from './IgnitionSheet';
import { IgnitionTile } from './IgnitionTile';
import type { IgnitionMonitorActions } from './types';

vi.mock('../../api/client', () => ({ getJson: vi.fn(), postForm: vi.fn() }));

function pausedPayload(): Record<string, unknown> {
  return {
    ok: true,
    ignition_monitor: {
      service: {
        active_state: 'active',
        sub_state: 'running',
        unit_file_state: 'enabled',
        running: true,
        enabled: true,
      },
      monitor: {
        version: 1,
        status: 'disabled',
        active: false,
        deadline: 1_700_007_200,
        remaining_seconds: 7_200,
        checked_at: 1_700_000_000,
      },
    },
  };
}

function activePayload(): Record<string, unknown> {
  const payload = pausedPayload();
  const ignition = payload.ignition_monitor as Record<string, unknown>;
  ignition.monitor = {
    version: 1,
    status: 'active',
    active: true,
    deadline: null,
    remaining_seconds: 0,
    checked_at: 1_700_000_000,
  };
  return payload;
}

beforeAll(() => {
  if (!HTMLDialogElement.prototype.showModal) {
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.setAttribute('open', '');
    };
  }
  if (!HTMLDialogElement.prototype.close) {
    HTMLDialogElement.prototype.close = function close() {
      this.removeAttribute('open');
      this.dispatchEvent(new Event('close'));
    };
  }
});

beforeEach(() => {
  vi.mocked(getJson).mockResolvedValue(pausedPayload());
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('ignition-monitor response decoder', () => {
  it('decodes systemd service state and the durable pause deadline', () => {
    const status = decodeIgnitionMonitorStatus(pausedPayload());

    expect(status.service).toMatchObject({
      running: true,
      enabled: true,
      activeState: 'active',
      subState: 'running',
    });
    expect(status.monitor).toMatchObject({
      status: 'disabled',
      active: false,
      deadline: 1_700_007_200,
      remainingSeconds: 7_200,
    });
  });

  it('accepts active monitoring and rejects inconsistent pause or service state', () => {
    expect(decodeIgnitionMonitorStatus(activePayload()).monitor.active).toBe(true);

    const badPause = pausedPayload();
    const ignition = badPause.ignition_monitor as Record<string, unknown>;
    const monitor = ignition.monitor as Record<string, unknown>;
    monitor.remaining_seconds = 7_199;
    expect(() => decodeIgnitionMonitorStatus(badPause)).toThrow(
      'paused ignition-monitor state contains inconsistent timing',
    );

    const badService = pausedPayload();
    const badIgnition = badService.ignition_monitor as Record<string, unknown>;
    const service = badIgnition.service as Record<string, unknown>;
    service.running = false;
    expect(() => decodeIgnitionMonitorStatus(badService)).toThrow(
      'ignition_monitor.service.running disagrees with systemd state',
    );
  });
});

describe('ignition polling and local countdown', () => {
  it('uses one shared poller and speeds up only while the sheet is open', async () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ open }) => useIgnitionMonitorStatus(open), {
      initialProps: { open: false },
    });
    await act(async () => Promise.resolve());

    expect(result.current.data?.monitor.remainingSeconds).toBe(7_200);
    expect(getJson).toHaveBeenCalledTimes(1);
    expect(getJson).toHaveBeenLastCalledWith('/api/ignition-monitor', expect.any(AbortSignal));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(IGNITION_TILE_POLL_INTERVAL_MS);
    });
    expect(getJson).toHaveBeenCalledTimes(2);

    rerender({ open: true });
    await act(async () => Promise.resolve());
    expect(getJson).toHaveBeenCalledTimes(3);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(IGNITION_SHEET_POLL_INTERVAL_MS);
    });
    expect(getJson).toHaveBeenCalledTimes(4);
  });

  it('projects the pause deadline locally without another request', () => {
    expect(ignitionRemainingSeconds(1_700_007_200, 1_700_000_000_000)).toBe(7_200);
    expect(formatIgnitionDuration(7_200)).toBe('2h 0m');
    expect(ignitionRemainingSeconds(1_700_007_200, 1_700_008_000_000)).toBe(0);
  });
});

describe('ignition views', () => {
  function actions(overrides: Partial<IgnitionMonitorActions> = {}): IgnitionMonitorActions {
    return {
      running: false,
      pause: vi.fn().mockResolvedValue(true),
      resume: vi.fn().mockResolvedValue(true),
      ...overrides,
    };
  }

  it('shows service and live pause timing on the tile', () => {
    const status = decodeIgnitionMonitorStatus(pausedPayload());
    const onOpen = vi.fn();
    render(
      <IgnitionTile
        status={status}
        error={null}
        refreshing={false}
        remainingSeconds={3_600}
        onOpen={onOpen}
      />,
    );

    expect(screen.getByText('Ignition handling resumes in 1h 0m')).toBeInTheDocument();
    expect(screen.getByText('Running · starts at boot')).toBeInTheDocument();
    expect(screen.getByText('Monitoring')).toBeInTheDocument();
    expect(screen.getByText('Paused · 1h 0m left')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open ignition monitor details' }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it('shows pause deadline details and live resume control', () => {
    const status = decodeIgnitionMonitorStatus(pausedPayload());
    const onRefresh = vi.fn().mockResolvedValue(status);
    const controls = actions();
    render(
      <IgnitionSheet
        open
        onClose={vi.fn()}
        status={status}
        error={null}
        refreshing={false}
        remainingSeconds={3_600}
        onRefresh={onRefresh}
        actions={controls}
      />,
    );

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Suppressed')).toBeInTheDocument();
    expect(screen.getAllByText('1h 0m').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: 'Resume now' }));
    expect(controls.resume).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole('button', { name: 'Refresh status' }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it('confirms a pause before sending the selected duration', () => {
    const status = decodeIgnitionMonitorStatus(activePayload());
    const controls = actions();
    const confirm = vi
      .spyOn(window, 'confirm')
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    render(
      <IgnitionSheet
        open
        onClose={vi.fn()}
        status={status}
        error={null}
        refreshing={false}
        remainingSeconds={null}
        onRefresh={vi.fn().mockResolvedValue(status)}
        actions={controls}
      />,
    );

    const pause = screen.getByRole('button', { name: 'Pause monitoring' });
    fireEvent.click(pause);
    expect(controls.pause).not.toHaveBeenCalled();
    fireEvent.click(pause);
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('2h 0m'));
    expect(controls.pause).toHaveBeenCalledWith(120);
  });
});
