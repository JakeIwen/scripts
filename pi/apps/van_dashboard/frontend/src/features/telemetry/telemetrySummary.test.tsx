import { render, renderHook, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { getJson } from '../../api/client';
import type { PollingState } from '../../hooks/usePollingResource';
import { TelemetryTileView } from './TelemetryTile';
import {
  convergeVoltageCheck,
  type TelemetryControls,
  VOLTAGE_CHECK_POLL_INTERVAL_MS,
} from './controls';
import {
  decodeTelemetrySummary,
  formatBatteryVoltage,
  type TelemetrySummary,
} from './telemetrySummary';
import { useTelemetrySummary } from './useTelemetrySummary';

vi.mock('../../api/client', () => ({ getJson: vi.fn(), postForm: vi.fn() }));

const telemetryPayload = {
  ok: true,
  battery: {
    available: true,
    value: 12.638,
    unit: 'V',
    source: 'live',
    observed_at: '2026-08-26T16:42:10-05:00',
    detail: 'Live telemetry',
  },
  service: {
    available: true,
    running: true,
  },
  check: {
    status: 'running',
    started_at: 1_700_000_000,
    completed_at: null,
    error: null,
  },
};

function resource(data: TelemetrySummary): PollingState<TelemetrySummary> {
  return {
    data,
    error: null,
    initialLoading: false,
    refreshing: false,
    lastUpdatedAt: Date.now(),
    refresh: vi.fn(async () => data),
  };
}

function controls(): TelemetryControls {
  return {
    serviceBusy: false,
    voltageCheckBusy: false,
    toggleService: vi.fn(async () => undefined),
    checkVoltage: vi.fn(async () => undefined),
  };
}

describe('decodeTelemetrySummary', () => {
  it('decodes the battery, service, and voltage-check snapshots', () => {
    const summary = decodeTelemetrySummary(telemetryPayload);

    expect(summary.battery.value).toBe(12.638);
    expect(summary.battery.observedAt).toBe('2026-08-26T16:42:10-05:00');
    expect(summary.service.running).toBe(true);
    expect(summary.check.status).toBe('running');
    expect(formatBatteryVoltage(summary.battery)).toBe('12.64 V');
  });

  it('requires an available battery to contain a numeric reading', () => {
    expect(() =>
      decodeTelemetrySummary({
        ...telemetryPayload,
        battery: { ...telemetryPayload.battery, value: null },
      }),
    ).toThrow('battery.value must be present when battery.available is true');
  });

  it('keeps a service-status failure as readable data', () => {
    const summary = decodeTelemetrySummary({
      ...telemetryPayload,
      service: {
        available: false,
        running: false,
        error: 'systemctl timed out',
      },
    });

    expect(summary.service.error).toBe('systemctl timed out');
  });
});

describe('useTelemetrySummary', () => {
  it('polls the summary endpoint and exposes decoded data', async () => {
    vi.mocked(getJson).mockResolvedValue(telemetryPayload);

    const { result } = renderHook(() => useTelemetrySummary());

    await waitFor(() => expect(result.current.data?.battery.value).toBe(12.638));
    expect(getJson).toHaveBeenCalledWith('/api/telemetry-summary', expect.any(AbortSignal));
  });
});

describe('voltage-check convergence', () => {
  it('reads authoritative status once a second until the worker completes', async () => {
    const running = decodeTelemetrySummary(telemetryPayload);
    const complete = {
      ...running,
      check: { ...running.check, status: 'complete' as const },
    };
    const refresh = vi
      .fn<PollingState<TelemetrySummary>['refresh']>()
      .mockResolvedValueOnce(running)
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(complete);
    const pause = vi.fn(async () => undefined);

    const result = await convergeVoltageCheck(refresh, 115_000, () => 0, pause);

    expect(result?.check.status).toBe('complete');
    expect(refresh).toHaveBeenCalledTimes(3);
    expect(pause).toHaveBeenNthCalledWith(1, VOLTAGE_CHECK_POLL_INTERVAL_MS);
    expect(pause).toHaveBeenNthCalledWith(2, VOLTAGE_CHECK_POLL_INTERVAL_MS);
  });
});

describe('TelemetryTileView', () => {
  it('shows telemetry with single-purpose service and voltage controls', () => {
    const actions = controls();
    render(
      <TelemetryTileView
        resource={resource(decodeTelemetrySummary(telemetryPayload))}
        controls={actions}
      />,
    );

    expect(screen.getByText('12.64 V')).toBeInTheDocument();
    expect(screen.getByText('Running now')).toBeInTheDocument();
    screen.getByRole('button', { name: 'Stop service' }).click();
    expect(actions.toggleService).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Checking voltage…' })).toBeDisabled();
  });

  it('labels the saved engine-off baseline distinctly', () => {
    const payload = {
      ...telemetryPayload,
      battery: {
        ...telemetryPayload.battery,
        source: 'engine_off',
        detail: 'Engine-off passive sample',
      },
    };
    render(
      <TelemetryTileView
        resource={resource(decodeTelemetrySummary(payload))}
        controls={controls()}
      />,
    );

    expect(screen.getByText('engine-off passive')).toBeInTheDocument();
  });
});
