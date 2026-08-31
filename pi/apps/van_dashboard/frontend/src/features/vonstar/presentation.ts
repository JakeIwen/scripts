import {
  VONSTAR_ACTIONS,
  type VonstarAccessState,
  type VonstarAttempt,
  type VonstarPresentation,
  type VonstarResult,
  type VonstarStatus,
} from './types';

export function describeVonstar(
  status: VonstarStatus | null,
  localBusy: boolean,
): VonstarPresentation {
  if (localBusy) {
    return {
      label: 'Working',
      summary: 'Requesting one guarded vehicle operation…',
      tone: 'warning',
    };
  }
  if (!status) {
    return {
      label: 'Loading',
      summary: 'Checking guarded vehicle lock controls…',
      tone: 'neutral',
    };
  }
  if (status.busy) {
    return {
      label: 'Busy',
      summary: 'Another guarded vehicle operation is in progress',
      tone: 'warning',
    };
  }
  if (status.mode === 'plan_only') {
    return {
      label: 'Plan only',
      summary: 'Plan-only service · vehicle actions are disabled',
      tone: 'warning',
    };
  }
  if (status.available && status.mode === 'execute') {
    return {
      label: 'Ready',
      summary: 'Guarded three-action vehicle lock controls',
      tone: 'good',
    };
  }
  return {
    label: 'Offline',
    summary: status.error ?? 'vOnStar service is unavailable',
    tone: 'bad',
  };
}

export function actionConfirmation(action: keyof typeof VONSTAR_ACTIONS): string {
  const definition = VONSTAR_ACTIONS[action];
  const warning = definition.validationNote ? `\n\n${definition.validationNote}` : '';
  return `vOnStar will ${definition.description}. Continue?${warning}`;
}

export const VONSTAR_ACCESS_CONFIRMATION = 'Wake the vehicle buses and check lock/door state?';

export function resultAttempt(result: VonstarResult | null): VonstarAttempt | null {
  if (!result) return null;
  const label = result.kind === 'access_state' ? 'Check Status' : result.label;
  return {
    label,
    state: result.ok ? 'ok' : 'failed',
    error: result.error,
  };
}

export function attemptLabel(attempt: VonstarAttempt | null): string {
  if (!attempt) return 'None';
  const state = attempt.state === 'ok' ? 'OK' : attempt.state === 'failed' ? 'FAILED' : 'WORKING';
  return `${attempt.label} · ${state}`;
}

export interface VonstarLockPresentation {
  label: string;
  detail: string;
  tone: 'neutral' | 'good' | 'warning';
}

export function describeLockState(state: VonstarAccessState | null): VonstarLockPresentation {
  if (!state) {
    return {
      label: 'Not checked this session',
      detail: 'Awaiting an explicit check',
      tone: 'neutral',
    };
  }
  const lock = state.lockDomains;
  if (lock.quality !== 'verified') {
    return {
      label: 'Lock state unknown',
      detail: `Insufficient lock evidence · ${lock.quality}`,
      tone: 'neutral',
    };
  }
  if (lock.state === 'locked') {
    return { label: 'All locked', detail: 'Verified lock-domain snapshot', tone: 'good' };
  }
  if (lock.state === 'front_unlocked_cargo_locked') {
    return {
      label: 'Front unlocked · cargo locked',
      detail: 'Verified lock-domain snapshot',
      tone: 'warning',
    };
  }
  if (lock.state === 'front_and_cargo_unlocked') {
    return {
      label: 'Front + cargo unlocked',
      detail: 'Verified lock-domain snapshot',
      tone: 'warning',
    };
  }
  return {
    label: 'Lock state unknown',
    detail: 'Insufficient lock evidence · unsupported verified state',
    tone: 'neutral',
  };
}

export function observedAtLabel(value: string): string {
  return new Date(value).toLocaleString([], {
    dateStyle: 'short',
    timeStyle: 'medium',
  });
}

export function driverDoorLabel(state: VonstarAccessState | null): string {
  if (!state) return 'Not checked';
  if (state.doors.driver.ajar === true) return 'Driver door open (candidate)';
  if (state.doors.driver.ajar === false) return 'Driver door closed (candidate)';
  return 'Driver door unknown';
}
