import { getJson } from '../../api/client';
import { decodeNetworkHistory, decodeNetworkIncident } from './decoders';
import type { HistoryQuery } from './types';

export function historyQueryString(query: HistoryQuery): string {
  const parameters = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '') parameters.set(key, String(value));
  }
  return parameters.toString();
}

export async function fetchNetworkHistory(query: HistoryQuery, signal?: AbortSignal) {
  return decodeNetworkHistory(
    await getJson(`/api/network-history?${historyQueryString(query)}`, signal),
  );
}

export async function fetchNetworkIncident(id: string, signal?: AbortSignal) {
  return decodeNetworkIncident(
    await getJson(`/api/network-history/incidents/${encodeURIComponent(id)}`, signal),
  );
}
