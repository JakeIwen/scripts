import { useCallback } from 'react';

import { useToast } from '../../components/ToastProvider';
import type { PollingState } from '../../hooks/usePollingResource';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { startTelemetryVoltageCheck, toggleTelemetryService } from './api';
import { formatBatteryVoltage, type TelemetrySummary } from './telemetrySummary';

export const VOLTAGE_CHECK_POLL_INTERVAL_MS = 1_000;
export const VOLTAGE_CHECK_TIMEOUT_MS = 115_000;

type RefreshTelemetry = PollingState<TelemetrySummary>['refresh'];
type Wait = (milliseconds: number) => Promise<void>;

export interface TelemetryControls {
  serviceBusy: boolean;
  voltageCheckBusy: boolean;
  toggleService: () => Promise<void>;
  checkVoltage: () => Promise<void>;
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

/**
 * Read the authoritative summary once a second until the voltage worker
 * settles. A failed status read is treated as inconclusive, never as success.
 */
export async function convergeVoltageCheck(
  refresh: RefreshTelemetry,
  deadline: number,
  now: () => number = Date.now,
  pause: Wait = wait,
): Promise<TelemetrySummary | null> {
  let summary = await refresh();

  while ((summary === null || summary.check.status === 'running') && now() < deadline) {
    await pause(VOLTAGE_CHECK_POLL_INTERVAL_MS);
    summary = await refresh();
  }

  return summary;
}

export function useTelemetryControls(resource: PollingState<TelemetrySummary>): TelemetryControls {
  const { showToast } = useToast();
  const serviceAction = useSingleFlightAction();
  const voltageAction = useSingleFlightAction();

  const toggleService = useCallback(async () => {
    await serviceAction.run(async () => {
      try {
        const result = await toggleTelemetryService();
        showToast(result.message);
      } catch (reason) {
        showToast(errorMessage(reason), 'error');
      } finally {
        // Display only the authoritative combined summary after a mutation.
        await resource.refresh();
      }
    });
  }, [resource.refresh, serviceAction, showToast]);

  const checkVoltage = useCallback(async () => {
    await voltageAction.run(async () => {
      const deadline = Date.now() + VOLTAGE_CHECK_TIMEOUT_MS;

      try {
        await startTelemetryVoltageCheck();
        const summary = await convergeVoltageCheck(resource.refresh, deadline);

        if (summary === null || summary.check.status === 'running') {
          throw new Error('Voltage check is still running; the tile will update when it finishes');
        }
        if (summary.check.status === 'error') {
          throw new Error(summary.check.error ?? 'Voltage check failed');
        }

        const voltage = formatBatteryVoltage(summary.battery);
        showToast(
          summary.battery.available
            ? `Voltage check complete · ${voltage}`
            : 'Voltage check completed; no reading is available',
        );
      } catch (reason) {
        showToast(errorMessage(reason), 'error');
      } finally {
        // Reconcile ambiguous failures, including a lost response after start.
        await resource.refresh();
      }
    });
  }, [resource.refresh, showToast, voltageAction]);

  return {
    serviceBusy: serviceAction.running,
    voltageCheckBusy: voltageAction.running,
    toggleService,
    checkVoltage,
  };
}
