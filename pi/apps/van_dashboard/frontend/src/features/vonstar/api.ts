import { getJson, postForm } from '../../api/client';
import {
  decodeVonstarAccessMutation,
  decodeVonstarActionMutation,
  decodeVonstarStatus,
} from './decoders';
import type {
  VonstarAccessResult,
  VonstarActionMutation,
  VonstarActionName,
  VonstarStatus,
} from './types';

export async function fetchVonstarStatus(signal: AbortSignal): Promise<VonstarStatus> {
  return decodeVonstarStatus(await getJson('/api/vonstar', signal));
}

/** Send exactly one fixed high-level action name; the backend creates the request ID. */
export async function performVonstarAction(
  action: VonstarActionName,
): Promise<VonstarActionMutation> {
  const payload = await postForm('/api/vonstar', { action });
  return decodeVonstarActionMutation(payload, action);
}

/** This intentionally sends an empty body and is called only after explicit confirmation. */
export async function requestVonstarAccessState(): Promise<VonstarAccessResult> {
  return decodeVonstarAccessMutation(await postForm('/api/vonstar/access-state'));
}
