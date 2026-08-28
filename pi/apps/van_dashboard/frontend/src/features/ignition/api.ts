import { getJson, postForm } from '../../api/client';
import { objectValue, stringValue } from '../../api/validation';
import { decodeIgnitionMonitorStatus } from './decoders';
import {
  IGNITION_MONITOR_MAX_MINUTES,
  type IgnitionMonitorMutationResult,
  type IgnitionMonitorStatus,
} from './types';

export async function fetchIgnitionMonitorStatus(
  signal: AbortSignal,
): Promise<IgnitionMonitorStatus> {
  const payload = await getJson('/api/ignition-monitor', signal);
  return decodeIgnitionMonitorStatus(payload);
}

function decodeIgnitionMutation(payload: unknown): IgnitionMonitorMutationResult {
  const response = objectValue(payload, 'ignition monitor mutation response');
  return {
    message: stringValue(response.message, 'ignition monitor mutation response.message'),
    status: decodeIgnitionMonitorStatus(payload),
  };
}

export async function pauseIgnitionMonitoring(
  minutes: number,
): Promise<IgnitionMonitorMutationResult> {
  if (!Number.isInteger(minutes) || minutes < 1 || minutes > IGNITION_MONITOR_MAX_MINUTES) {
    throw new RangeError(
      `Ignition pause must be 1 to ${IGNITION_MONITOR_MAX_MINUTES} whole minutes`,
    );
  }
  const payload = await postForm('/api/ignition-monitor/disable', { minutes });
  return decodeIgnitionMutation(payload);
}

export async function resumeIgnitionMonitoring(): Promise<IgnitionMonitorMutationResult> {
  const payload = await postForm('/api/ignition-monitor/enable', {});
  return decodeIgnitionMutation(payload);
}
