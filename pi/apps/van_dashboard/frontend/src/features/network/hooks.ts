import { useEffect, useState } from 'react';

import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { fetchActiveConnectivity, fetchOpenWrtClients, fetchSpeedtestStatus } from './api';
import { measureWanThroughput, type WanThroughput } from './throughput';
import type { ConnectivityStatus, OpenWrtClientStatus, SpeedtestStatus, WanTraffic } from './types';

export const ACTIVE_CONNECTIVITY_INTERVAL_MS = 1_000;
export const OPENWRT_CLIENT_INTERVAL_MS = 20_000;
export const RUNNING_SPEEDTEST_INTERVAL_MS = 1_000;
export const IDLE_SPEEDTEST_INTERVAL_MS = 30_000;

export function useActiveConnectivity(): PollingState<ConnectivityStatus> {
  return usePollingResource({
    load: fetchActiveConnectivity,
    intervalMs: ACTIVE_CONNECTIVITY_INTERVAL_MS,
  });
}

export function useSpeedtestStatus(): PollingState<SpeedtestStatus> {
  const [intervalMs, setIntervalMs] = useState(IDLE_SPEEDTEST_INTERVAL_MS);
  const resource = usePollingResource({
    load: fetchSpeedtestStatus,
    intervalMs,
  });

  useEffect(() => {
    const nextInterval =
      resource.data?.status === 'running'
        ? RUNNING_SPEEDTEST_INTERVAL_MS
        : IDLE_SPEEDTEST_INTERVAL_MS;
    setIntervalMs((current) => (current === nextInterval ? current : nextInterval));
  }, [resource.data?.status]);

  return resource;
}

export function useOpenWrtClients(sheetOpen: boolean): PollingState<OpenWrtClientStatus> {
  return usePollingResource({
    load: fetchOpenWrtClients,
    intervalMs: OPENWRT_CLIENT_INTERVAL_MS,
    enabled: sheetOpen,
  });
}

/**
 * Derive current internet throughput from successive collector samples. Polling
 * repeats a sample until the collector runs again, so only a new router clock
 * reading starts a new measurement window.
 */
export function useWanThroughput(traffic: WanTraffic | null | undefined): WanThroughput | null {
  const [latest, setLatest] = useState<{
    sample: WanTraffic | null;
    throughput: WanThroughput | null;
  }>({ sample: null, throughput: null });

  // Derived from the previous render's sample, so it updates in the same render.
  if (traffic && traffic.uptimeSeconds !== latest.sample?.uptimeSeconds) {
    setLatest({
      sample: traffic,
      throughput: latest.sample ? measureWanThroughput(latest.sample, traffic) : null,
    });
  }
  return latest.throughput;
}

export interface OpenWrtResources {
  connectivity: PollingState<ConnectivityStatus>;
  speedtest: PollingState<SpeedtestStatus>;
  clients: PollingState<OpenWrtClientStatus>;
}

export function useOpenWrtResources(sheetOpen: boolean): OpenWrtResources {
  return {
    connectivity: useActiveConnectivity(),
    speedtest: useSpeedtestStatus(),
    clients: useOpenWrtClients(sheetOpen),
  };
}
