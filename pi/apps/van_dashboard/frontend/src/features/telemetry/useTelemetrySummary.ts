import { getJson } from '../../api/client';
import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { decodeTelemetrySummary, type TelemetrySummary } from './telemetrySummary';

export const TELEMETRY_POLL_INTERVAL_MS = 15_000;

export async function loadTelemetrySummary(signal: AbortSignal): Promise<TelemetrySummary> {
  const payload = await getJson('/api/telemetry-summary', signal);
  return decodeTelemetrySummary(payload);
}

export function useTelemetrySummary(): PollingState<TelemetrySummary> {
  return usePollingResource({
    load: loadTelemetrySummary,
    intervalMs: TELEMETRY_POLL_INTERVAL_MS,
  });
}
