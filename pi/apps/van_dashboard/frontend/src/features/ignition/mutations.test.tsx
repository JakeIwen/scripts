import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { getJson, postForm } from '../../api/client';
import { ToastProvider } from '../../components/ToastProvider';
import { pauseIgnitionMonitoring, resumeIgnitionMonitoring } from './api';
import { decodeIgnitionMonitorStatus } from './decoders';
import { IgnitionDurationEditor } from './IgnitionDurationEditor';
import { useIgnitionMonitorActions } from './useIgnitionMonitorActions';

vi.mock('../../api/client', () => ({ getJson: vi.fn(), postForm: vi.fn() }));

function monitorPayload(paused: boolean) {
  const checkedAt = 1_700_000_000;
  return {
    ok: true,
    message: paused ? 'Ignition monitoring paused' : 'Ignition monitoring resumed',
    ignition_monitor: {
      service: {
        active_state: 'active',
        sub_state: 'running',
        unit_file_state: 'enabled',
        running: true,
        enabled: true,
      },
      monitor: paused
        ? {
            version: 1,
            status: 'disabled',
            active: false,
            deadline: checkedAt + 7_200,
            remaining_seconds: 7_200,
            checked_at: checkedAt,
          }
        : {
            version: 1,
            status: 'active',
            active: true,
            deadline: null,
            remaining_seconds: 0,
            checked_at: checkedAt,
          },
    },
  };
}

function wrapper({ children }: { children: React.ReactNode }) {
  return <ToastProvider>{children}</ToastProvider>;
}

beforeEach(() => {
  vi.mocked(postForm).mockResolvedValue(monitorPayload(true));
  vi.mocked(getJson).mockResolvedValue(monitorPayload(false));
});

describe('ignition-monitor mutation API', () => {
  it('uses exact disable minutes and empty enable forms', async () => {
    await pauseIgnitionMonitoring(120);
    vi.mocked(postForm).mockResolvedValueOnce(monitorPayload(false));
    await resumeIgnitionMonitoring();

    expect(postForm).toHaveBeenNthCalledWith(1, '/api/ignition-monitor/disable', { minutes: 120 });
    expect(postForm).toHaveBeenNthCalledWith(2, '/api/ignition-monitor/enable', {});
  });

  it('rejects an invalid duration before sending a request', async () => {
    await expect(pauseIgnitionMonitoring(0)).rejects.toThrow(/1 to .* whole minutes/);
    await expect(pauseIgnitionMonitoring(1.5)).rejects.toThrow(/whole minutes/);
    expect(postForm).not.toHaveBeenCalled();
  });
});

describe('useIgnitionMonitorActions', () => {
  it('adopts authoritative responses and performs one GET after a failed mutation', async () => {
    const onAuthoritativeStatus = vi.fn();
    const paused = decodeIgnitionMonitorStatus(monitorPayload(true));
    const active = decodeIgnitionMonitorStatus(monitorPayload(false));
    const { result } = renderHook(() => useIgnitionMonitorActions({ onAuthoritativeStatus }), {
      wrapper,
    });

    await act(async () => {
      await expect(result.current.pause(120)).resolves.toBe(true);
    });
    expect(onAuthoritativeStatus).toHaveBeenLastCalledWith(paused);

    vi.mocked(postForm).mockRejectedValueOnce(new Error('ignitionmonctl failed'));
    await act(async () => {
      await expect(result.current.resume()).rejects.toThrow('ignitionmonctl failed');
    });
    expect(postForm).toHaveBeenCalledTimes(2);
    expect(getJson).toHaveBeenCalledOnce();
    expect(getJson).toHaveBeenCalledWith('/api/ignition-monitor', expect.any(AbortSignal));
    expect(onAuthoritativeStatus).toHaveBeenLastCalledWith(active);
  });

  it('does not retry and rejects a second request while one is in flight', async () => {
    let release: ((payload: unknown) => void) | undefined;
    vi.mocked(postForm).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const { result } = renderHook(
      () => useIgnitionMonitorActions({ onAuthoritativeStatus: vi.fn() }),
      { wrapper },
    );

    let first!: Promise<boolean>;
    let second!: Promise<boolean>;
    act(() => {
      first = result.current.pause(30);
      second = result.current.resume();
    });
    await expect(second).resolves.toBe(false);
    expect(postForm).toHaveBeenCalledTimes(1);

    await act(async () => {
      release?.(monitorPayload(true));
      await first;
    });
    expect(postForm).toHaveBeenCalledTimes(1);
  });
});

describe('IgnitionDurationEditor', () => {
  it('supports typed amounts, mouse-friendly presets, units, and slider input', () => {
    const onChange = vi.fn();
    const view = render(
      <IgnitionDurationEditor minutes={120} disabled={false} onChange={onChange} />,
    );

    fireEvent.change(screen.getByRole('spinbutton', { name: 'Amount' }), {
      target: { value: '3' },
    });
    expect(onChange).toHaveBeenLastCalledWith(180);

    fireEvent.click(screen.getByRole('button', { name: '8 hours' }));
    expect(onChange).toHaveBeenLastCalledWith(480);

    view.rerender(<IgnitionDurationEditor minutes={480} disabled={false} onChange={onChange} />);
    fireEvent.input(screen.getByRole('slider', { name: 'Pause duration in hours' }), {
      target: { value: '10' },
    });
    expect(onChange).toHaveBeenLastCalledWith(600);

    fireEvent.change(screen.getByRole('combobox', { name: 'Unit' }), {
      target: { value: 'days' },
    });
    expect(onChange).toHaveBeenLastCalledWith(1_440);
  });
});
