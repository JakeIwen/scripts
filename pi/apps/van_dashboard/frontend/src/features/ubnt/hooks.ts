import { useEffect, useState } from 'react';

import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { fetchUbntWifiStatus } from './api';
import type { UbntWifiStatus } from './types';

export const UBNT_IDLE_INTERVAL_MS = 30_000;
export const UBNT_OPERATION_INTERVAL_MS = 1_200;

export function useUbntWifiStatus(): PollingState<UbntWifiStatus> {
  const [intervalMs, setIntervalMs] = useState(UBNT_IDLE_INTERVAL_MS);
  const resource = usePollingResource({
    load: fetchUbntWifiStatus,
    intervalMs,
  });

  useEffect(() => {
    const next =
      resource.data?.operation.status === 'running'
        ? UBNT_OPERATION_INTERVAL_MS
        : UBNT_IDLE_INTERVAL_MS;
    setIntervalMs((current) => (current === next ? current : next));
  }, [resource.data?.operation.status]);

  return resource;
}
