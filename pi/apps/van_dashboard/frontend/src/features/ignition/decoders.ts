import { booleanValue, nullableNumber, objectValue, stringValue } from '../../api/validation';
import type { IgnitionMonitorState, IgnitionMonitorStatus, IgnitionServiceStatus } from './types';

function trueValue(value: unknown, label: string): true {
  if (value !== true) throw new TypeError(`${label} must be true`);
  return true;
}

function nonemptyString(value: unknown, label: string): string {
  const text = stringValue(value, label);
  if (!text.trim()) throw new TypeError(`${label} must not be empty`);
  return text;
}

function integer(value: unknown, label: string, minimum: number): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < minimum) {
    throw new TypeError(`${label} must be a whole number of at least ${minimum}`);
  }
  return value;
}

function nullableInteger(value: unknown, label: string): number | null {
  const number = nullableNumber(value, label);
  if (number !== null && !Number.isInteger(number)) {
    throw new TypeError(`${label} must be a whole number or null`);
  }
  return number;
}

function decodeService(value: unknown): IgnitionServiceStatus {
  const service = objectValue(value, 'ignition_monitor.service');
  const activeState = nonemptyString(service.active_state, 'ignition_monitor.service.active_state');
  const subState = nonemptyString(service.sub_state, 'ignition_monitor.service.sub_state');
  const unitFileState = nonemptyString(
    service.unit_file_state,
    'ignition_monitor.service.unit_file_state',
  );
  const running = booleanValue(service.running, 'ignition_monitor.service.running');
  const enabled = booleanValue(service.enabled, 'ignition_monitor.service.enabled');

  if (running !== (activeState === 'active' && subState === 'running')) {
    throw new TypeError('ignition_monitor.service.running disagrees with systemd state');
  }
  if (enabled !== ['enabled', 'enabled-runtime'].includes(unitFileState)) {
    throw new TypeError('ignition_monitor.service.enabled disagrees with unit-file state');
  }

  return { activeState, subState, unitFileState, running, enabled };
}

/** Decode the exact service and durable-pause state from GET /api/ignition-monitor. */
export function decodeIgnitionMonitorStatus(payload: unknown): IgnitionMonitorStatus {
  const response = objectValue(payload, 'ignition monitor response');
  trueValue(response.ok, 'ignition monitor response.ok');
  const status = objectValue(response.ignition_monitor, 'ignition_monitor');
  const monitor = objectValue(status.monitor, 'ignition_monitor.monitor');

  if (monitor.version !== 1) {
    throw new TypeError('ignition_monitor.monitor.version must be 1');
  }
  const monitorState = stringValue(monitor.status, 'ignition_monitor.monitor.status');
  if (monitorState !== 'active' && monitorState !== 'disabled') {
    throw new TypeError(
      `ignition_monitor.monitor.status has an unsupported value: ${monitorState}`,
    );
  }

  const active = booleanValue(monitor.active, 'ignition_monitor.monitor.active');
  const deadline = nullableInteger(monitor.deadline, 'ignition_monitor.monitor.deadline');
  const remainingSeconds = integer(
    monitor.remaining_seconds,
    'ignition_monitor.monitor.remaining_seconds',
    0,
  );
  const checkedAt = integer(monitor.checked_at, 'ignition_monitor.monitor.checked_at', 1);

  if (monitorState === 'active') {
    if (!active || deadline !== null || remainingSeconds !== 0) {
      throw new TypeError('active ignition-monitor state contains inconsistent timing');
    }
  } else if (
    active ||
    deadline === null ||
    deadline <= checkedAt ||
    deadline - checkedAt !== remainingSeconds
  ) {
    throw new TypeError('paused ignition-monitor state contains inconsistent timing');
  }

  return {
    service: decodeService(status.service),
    monitor: {
      version: 1,
      status: monitorState as IgnitionMonitorState,
      active,
      deadline,
      remainingSeconds,
      checkedAt,
    },
  };
}
