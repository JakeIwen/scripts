import { getJson, postForm } from '../../api/client';
import { nullableNumber, nullableString, objectValue, stringValue } from '../../api/validation';
import type {
  DashboardRestartResult,
  SystemPowerAction,
  SystemPowerOperation,
  SystemPowerOperationStatus,
  SystemPowerRequestResult,
} from './types';

const OPERATION_STATES = [
  'idle',
  'running',
  'complete',
  'error',
] as const satisfies readonly SystemPowerOperationStatus[];
const POWER_ACTIONS = ['reboot', 'power-down'] as const satisfies readonly SystemPowerAction[];

function oneOf<T extends string>(value: unknown, choices: readonly T[], label: string): T {
  const text = stringValue(value, label);
  if (!choices.includes(text as T)) {
    throw new TypeError(`${label} has an unsupported value: ${text}`);
  }
  return text as T;
}

function decodeOperation(value: unknown): SystemPowerOperation {
  const operation = objectValue(value, 'system_power');
  return {
    status: oneOf(operation.status, OPERATION_STATES, 'system_power.status'),
    action:
      operation.action === null
        ? null
        : oneOf(operation.action, POWER_ACTIONS, 'system_power.action'),
    startedAt: nullableNumber(operation.started_at, 'system_power.started_at'),
    completedAt: nullableNumber(operation.completed_at, 'system_power.completed_at'),
    error: nullableString(operation.error, 'system_power.error'),
  };
}

function responseMessage(value: unknown, label: string): string {
  const response = objectValue(value, label);
  if (response.ok !== true) throw new TypeError(`${label}.ok must be true`);
  return stringValue(response.message, `${label}.message`);
}

export async function fetchSystemPower(signal?: AbortSignal): Promise<SystemPowerOperation> {
  const payload = await getJson('/api/system-power', signal);
  const response = objectValue(payload, 'system power response');
  if (response.ok !== true) throw new TypeError('system power response.ok must be true');
  return decodeOperation(response.system_power);
}

export async function requestSystemPower(
  action: SystemPowerAction,
): Promise<SystemPowerRequestResult> {
  const payload = await postForm('/api/system-power', { action, confirmation: action });
  const response = objectValue(payload, 'system power request response');
  return {
    message: responseMessage(payload, 'system power request response'),
    operation: decodeOperation(response.system_power),
  };
}

export async function requestDashboardRestart(): Promise<DashboardRestartResult> {
  const payload = await postForm('/api/dashboard-service/restart', {
    confirmation: 'restart-dashboard',
  });
  const response = objectValue(payload, 'dashboard restart response');
  const restart = objectValue(response.dashboard_restart, 'dashboard_restart');
  const scheduledAt = nullableNumber(restart.scheduled_at, 'dashboard_restart.scheduled_at');
  if (scheduledAt === null) throw new TypeError('dashboard_restart.scheduled_at is required');
  return {
    message: responseMessage(payload, 'dashboard restart response'),
    scheduledAt,
  };
}

export async function probeDashboard(signal?: AbortSignal): Promise<void> {
  await getJson('/api/status', signal);
}
