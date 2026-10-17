import { useEffect, useRef, useState } from 'react';

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
  const previousRef = useRef<WanTraffic | null>(null);
  const [throughput, setThroughput] = useState<WanThroughput | null>(null);

  useEffect(() => {
    if (!traffic) return;
    const previous = previousRef.current;
    if (previous?.uptimeSeconds === traffic.uptimeSeconds) return;
    previousRef.current = traffic;
    setThroughput(previous ? measureWanThroughput(previous, traffic) : null);
  }, [traffic]);

  return throughput;
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
