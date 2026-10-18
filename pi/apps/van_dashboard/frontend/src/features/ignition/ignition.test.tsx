import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { getJson } from '../../api/client';
import { formatIgnitionDuration, ignitionRemainingSeconds } from './countdown';
import { decodeIgnitionMonitorStatus } from './decoders';
import { IgnitionSheet } from './IgnitionSheet';
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
});

describe('ignition polling and local countdown', () => {
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

  it('wires the live resume and refresh controls', () => {
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
