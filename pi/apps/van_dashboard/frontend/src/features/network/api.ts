import { getJson, postForm } from '../../api/client';
import { objectValue, stringValue } from '../../api/validation';
import {
  decodeConnectivityResponse,
  decodeOpenWrtClientsResponse,
  decodeSpeedtestResponse,
} from './decoders';
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

export interface SpeedtestStartResult {
  message: string;
  status: SpeedtestStatus;
}

/** Start one speed test with the endpoint's exact empty form. */
export async function startSpeedtest(): Promise<SpeedtestStartResult> {
  const payload = await postForm('/api/speedtest');
  const response = objectValue(payload, 'speed test start response');
  return {
    message: stringValue(response.message, 'speed test start response.message'),
    status: decodeSpeedtestResponse(payload),
  };
}
