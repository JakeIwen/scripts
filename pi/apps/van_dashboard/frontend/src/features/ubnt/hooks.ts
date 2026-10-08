import { useEffect, useState } from 'react';

import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { fetchUbntWifiStatus } from './api';
import type { UbntWifiStatus } from './types';

export const UBNT_IDLE_INTERVAL_MS = 10_000;
export const UBNT_OPERATION_INTERVAL_MS = 1_200;

export function useUbntWifiStatus(): PollingState<UbntWifiStatus> {
  const [intervalMs, setIntervalMs] = useState(UBNT_IDLE_INTERVAL_MS);
  const resource = usePollingResource({
    load: fetchUbntWifiStatus,
    intervalMs,
  });

  useEffect(() => {
    // HTTP returns the cached snapshot while the antenna read runs separately.
    // Collect its result promptly, including after a mutation has timed out.
    const status = resource.data;
    const next =
      status?.operation.status === 'running' ||
      status?.statusRefreshing ||
      status?.starlinkPending ||
      status?.state.selectorRunning ||
      status?.lastError ||
      resource.error
        ? UBNT_OPERATION_INTERVAL_MS
        : UBNT_IDLE_INTERVAL_MS;
    setIntervalMs((current) => (current === next ? current : next));
  }, [resource.data, resource.error]);

  return resource;
}
