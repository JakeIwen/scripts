import { getJson, postForm } from '../../api/client';
import {
  decodeConnectivityResponse,
  decodeOpenWrtClientsResponse,
  decodeSpeedtestResponse,
  decodeStartSpeedtestResponse,
  type SpeedtestStartResult,
} from './schema';
import type { ConnectivityStatus, OpenWrtClientStatus, SpeedtestStatus } from './types';

export async function fetchActiveConnectivity(signal: AbortSignal): Promise<ConnectivityStatus> {
  const payload = await getJson('/api/connectivity?active=1', signal);
  return decodeConnectivityResponse(payload);
}

export async function fetchOpenWrtClients(signal: AbortSignal): Promise<OpenWrtClientStatus> {
  const payload = await getJson('/api/openwrt/clients', signal);
  return decodeOpenWrtClientsResponse(payload);
}

export async function fetchSpeedtestStatus(signal: AbortSignal): Promise<SpeedtestStatus> {
  const payload = await getJson('/api/speedtest', signal);
  return decodeSpeedtestResponse(payload);
}

export type { SpeedtestStartResult };

/** Start one speed test with the endpoint's exact empty form. */
export async function startSpeedtest(): Promise<SpeedtestStartResult> {
  const payload = await postForm('/api/speedtest');
  return decodeStartSpeedtestResponse(payload);
}
