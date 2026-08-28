import { getJson } from '../../api/client';
import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { decodeDashboardStatus, type DashboardStatus } from './dashboardStatus';

export const STATUS_POLL_INTERVAL_MS = 5_000;

export async function loadDashboardStatus(signal: AbortSignal): Promise<DashboardStatus> {
  const payload = await getJson('/api/status', signal);
  return decodeDashboardStatus(payload);
}

/**
 * The status endpoint is shared by the COP tile and dashboard header. App
 * should call this hook once and pass the returned resource to the tile.
 */
export function useDashboardStatus(): PollingState<DashboardStatus> {
  return usePollingResource({
    load: loadDashboardStatus,
    intervalMs: STATUS_POLL_INTERVAL_MS,
  });
}
