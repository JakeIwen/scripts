import {
  arrayValue,
  booleanValue,
  nullableBoolean,
  nullableNumber,
  nullableString,
  numberValue,
  objectValue,
  stringValue,
} from '../../api/validation';
import {
  VONSTAR_ACTION_NAMES,
  VONSTAR_ACTIONS,
  type VonstarAccessResult,
  type VonstarAccessState,
  type VonstarActionMutation,
  type VonstarActionName,
  type VonstarActionResult,
  type VonstarDoorState,
  type VonstarMode,
  type VonstarResult,
  type VonstarStatus,
} from './types';

function literal<T extends string>(value: unknown, expected: T, label: string): T {
  if (value !== expected) throw new TypeError(`${label} must be ${expected}`);
  return expected;
}

function boundedString(
  value: unknown,
  label: string,
  maximum: number,
  { empty = false }: { empty?: boolean } = {},
): string {
  const text = stringValue(value, label);
  if ((!empty && !text.trim()) || text.length > maximum) {
    throw new TypeError(
      `${label} must be ${empty ? '' : 'nonempty and '}at most ${maximum} characters`,
    );
  }
  return text;
}

function optionalBoundedString(value: unknown, label: string, maximum = 512): string | undefined {
  return value === undefined ? undefined : boundedString(value, label, maximum);
}

function decodeMode(value: unknown, label: string): VonstarMode {
  const mode = stringValue(value, label);
  if (mode !== 'execute' && mode !== 'plan_only' && mode !== 'unavailable') {
    throw new TypeError(`${label} has an unsupported value: ${mode}`);
  }
  return mode;
}

function decodeActionName(value: unknown, label: string): VonstarActionName {
  const action = stringValue(value, label);
  if (!VONSTAR_ACTION_NAMES.includes(action as VonstarActionName)) {
    throw new TypeError(`${label} has an unsupported value: ${action}`);
  }
  return action as VonstarActionName;
}

function decodeActionCatalog(value: unknown): void {
  const actions = objectValue(value, 'vonstar.actions');
  const keys = Object.keys(actions).sort();
  const expected = [...VONSTAR_ACTION_NAMES].sort();
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) {
    throw new TypeError('vonstar.actions must contain exactly the three supported actions');
  }

  for (const action of VONSTAR_ACTION_NAMES) {
    const item = objectValue(actions[action], `vonstar.actions.${action}`);
    literal(item.label, VONSTAR_ACTIONS[action].label, `vonstar.actions.${action}.label`);
    literal(
      item.validation,
      VONSTAR_ACTIONS[action].validation,
      `vonstar.actions.${action}.validation`,
    );
  }
}

function decodeDoor(value: unknown, label: string): VonstarDoorState {
  const door = objectValue(value, label);
  return {
    locked: nullableBoolean(door.locked, `${label}.locked`),
    ajar: nullableBoolean(door.ajar, `${label}.ajar`),
    reportedClosed: nullableBoolean(door.reported_closed, `${label}.reported_closed`),
    physicalStateObservable: nullableBoolean(
      door.physical_state_observable,
      `${label}.physical_state_observable`,
    ),
    lockQuality: boundedString(door.lock_quality, `${label}.lock_quality`, 128),
    ajarQuality: boundedString(door.ajar_quality, `${label}.ajar_quality`, 128),
  };
}

function verifiedLockStateIsConsistent(
  state: string,
  frontLocked: boolean | null,
  cargoLocked: boolean | null,
): boolean {
  if (state === 'locked') return frontLocked === true && cargoLocked === true;
  if (state === 'front_unlocked_cargo_locked') {
    return frontLocked === false && cargoLocked === true;
  }
  if (state === 'front_and_cargo_unlocked') {
    return frontLocked === false && cargoLocked === false;
  }
  return false;
}

export function decodeVonstarAccessState(value: unknown): VonstarAccessState {
  const state = objectValue(value, 'access_state');
  const observedAt = boundedString(state.observed_at, 'access_state.observed_at', 128);
  if (Number.isNaN(new Date(observedAt).getTime())) {
    throw new TypeError('access_state.observed_at must be a valid timestamp');
  }

  const lockDomains = objectValue(state.lock_domains, 'access_state.lock_domains');
  const lockState = boundedString(lockDomains.state, 'access_state.lock_domains.state', 64);
  const lockQuality = boundedString(lockDomains.quality, 'access_state.lock_domains.quality', 64);
  const frontLocked = nullableBoolean(
    lockDomains.front_locked,
    'access_state.lock_domains.front_locked',
  );
  const cargoLocked = nullableBoolean(
    lockDomains.cargo_locked,
    'access_state.lock_domains.cargo_locked',
  );
  if (
    lockQuality === 'verified' &&
    !verifiedLockStateIsConsistent(lockState, frontLocked, cargoLocked)
  ) {
    throw new TypeError('verified access_state.lock_domains is inconsistent');
  }

  const doors = objectValue(state.doors, 'access_state.doors');
  const wake = objectValue(state.wake, 'access_state.wake');
  const wakeCount = numberValue(wake.count, 'access_state.wake.count');
  if (wakeCount !== 0 && wakeCount !== 1) {
    throw new TypeError('access_state.wake.count must be 0 or 1');
  }

  const limitations = arrayValue(state.limitations, 'access_state.limitations');
  if (limitations.length > 32) {
    throw new TypeError('access_state.limitations must contain at most 32 items');
  }

  return {
    observedAt,
    complete: booleanValue(state.complete, 'access_state.complete'),
    lockDomains: {
      state: lockState,
      quality: lockQuality,
      frontLocked,
      cargoLocked,
    },
    doors: {
      driver: decodeDoor(doors.driver, 'access_state.doors.driver'),
      passenger: decodeDoor(doors.passenger, 'access_state.doors.passenger'),
      sliding: decodeDoor(doors.sliding, 'access_state.doors.sliding'),
      rear: decodeDoor(doors.rear, 'access_state.doors.rear'),
    },
    wake: {
      count: wakeCount,
      restoredPassiveAfterSampling: booleanValue(
        wake.restored_passive_after_sampling,
        'access_state.wake.restored_passive_after_sampling',
      ),
    },
    limitations: limitations.map((item, index) =>
      boundedString(item, `access_state.limitations[${index}]`, 512),
    ),
  };
}

export function decodeVonstarActionResult(
  value: unknown,
  expectedAction?: VonstarActionName,
): VonstarActionResult {
  const result = objectValue(value, 'vonstar action result');
  const action = decodeActionName(result.action, 'vonstar action result.action');
  if (expectedAction !== undefined && action !== expectedAction) {
    throw new TypeError('vonstar action result.action does not match the requested action');
  }
  const label = literal(result.label, VONSTAR_ACTIONS[action].label, 'vonstar action result.label');
  const ok = booleanValue(result.ok, 'vonstar action result.ok');
  const error = optionalBoundedString(result.error, 'vonstar action result.error');
  if (ok && error !== undefined) {
    throw new TypeError('successful vonstar action result must not contain an error');
  }
  return {
    kind: 'action',
    action,
    label,
    ok,
    startedAt: optionalBoundedString(result.started_at, 'vonstar action result.started_at'),
    completedAt: optionalBoundedString(result.completed_at, 'vonstar action result.completed_at'),
    error,
  };
}

export function decodeVonstarAccessResult(
  value: unknown,
  { requireSuccess = false }: { requireSuccess?: boolean } = {},
): VonstarAccessResult {
  const result = objectValue(value, 'vonstar access-state result');
  literal(result.operation, 'access_state', 'vonstar access-state result.operation');
  const ok = booleanValue(result.ok, 'vonstar access-state result.ok');
  if (requireSuccess && !ok) {
    throw new TypeError('vonstar access-state result must be successful');
  }
  const error = optionalBoundedString(result.error, 'vonstar access-state result.error');
  if (ok && error !== undefined) {
    throw new TypeError('successful vonstar access-state result must not contain an error');
  }
  if (!ok && error === undefined) {
    throw new TypeError('failed vonstar access-state result must contain an error');
  }
  return {
    kind: 'access_state',
    operation: 'access_state',
    ok,
    accessState: ok ? decodeVonstarAccessState(result.access_state) : undefined,
    startedAt: optionalBoundedString(result.started_at, 'vonstar access-state result.started_at'),
    completedAt: optionalBoundedString(
      result.completed_at,
      'vonstar access-state result.completed_at',
    ),
    error,
  };
}

function decodeResult(value: unknown): VonstarResult {
  const result = objectValue(value, 'vonstar.last_result');
  return result.operation === 'access_state'
    ? decodeVonstarAccessResult(result)
    : decodeVonstarActionResult(result);
}

function decodeStatusObject(value: unknown): VonstarStatus {
  const status = objectValue(value, 'vonstar');
  literal(status.service, 'vonstar', 'vonstar.service');
  const mode = decodeMode(status.mode, 'vonstar.mode');
  const available = booleanValue(status.available, 'vonstar.available');
  if (available !== (mode === 'execute')) {
    throw new TypeError('vonstar.available disagrees with vonstar.mode');
  }
  decodeActionCatalog(status.actions);

  const cooldownSeconds = nullableNumber(status.cooldown_seconds, 'vonstar.cooldown_seconds');
  if (cooldownSeconds !== null && (cooldownSeconds < 0 || cooldownSeconds > 60)) {
    throw new TypeError('vonstar.cooldown_seconds must be between 0 and 60');
  }
  if ((mode === 'unavailable') !== (cooldownSeconds === null)) {
    throw new TypeError('vonstar.cooldown_seconds disagrees with vonstar.mode');
  }

  const error = status.error === undefined ? null : nullableString(status.error, 'vonstar.error');
  if (error !== null) boundedString(error, 'vonstar.error', 512);
  if ((mode === 'unavailable') !== (error !== null)) {
    throw new TypeError('vonstar.error disagrees with vonstar.mode');
  }

  return {
    service: 'vonstar',
    available,
    mode,
    busy: booleanValue(status.busy, 'vonstar.busy'),
    cooldownSeconds,
    lastResult: status.last_result === null ? null : decodeResult(status.last_result),
    error,
  };
}

/** Decode the exact browser-facing response from GET /api/vonstar. */
export function decodeVonstarStatus(payload: unknown): VonstarStatus {
  const response = objectValue(payload, 'vonstar response');
  if (response.ok !== true) throw new TypeError('vonstar response.ok must be true');
  return decodeStatusObject(response.vonstar);
}

export function decodeVonstarActionMutation(
  payload: unknown,
  expectedAction: VonstarActionName,
): VonstarActionMutation {
  const response = objectValue(payload, 'vonstar mutation response');
  if (response.ok !== true) throw new TypeError('vonstar mutation response.ok must be true');
  decodeStatusObject(response.vonstar);
  return {
    message: boundedString(response.message, 'vonstar mutation response.message', 512),
    result: decodeVonstarActionResult(response.result, expectedAction),
  };
}

export function decodeVonstarAccessMutation(payload: unknown): VonstarAccessResult {
  return decodeVonstarAccessResult(payload, { requireSuccess: true });
}
