import { getJson, postJson } from '../../../api/client';
import {
  decodeMeteredStatus,
  decodePolicyResponse,
  decodeSSIDResponse,
  type PolicyEdit,
} from './schema';

const METERED_API = '/api/metered';

export async function fetchMeteredStatus(signal: AbortSignal) {
  return decodeMeteredStatus(await getJson(METERED_API, signal));
}

export async function editSSID(op: 'add' | 'remove', ssid: string, revision: number) {
  return decodeSSIDResponse(await postJson(`${METERED_API}/ssids`, { op, ssid, revision }));
}

export async function editPolicy(edit: PolicyEdit, revision: number) {
  return decodePolicyResponse(await postJson(`${METERED_API}/policy`, { ...edit, revision }));
}
