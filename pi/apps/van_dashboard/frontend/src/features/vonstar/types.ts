import type { StatusTone } from '../../components/StatusPill';

export const VONSTAR_ACTION_NAMES = ['lock_all', 'unlock_front', 'unlock_cargo'] as const;

export type VonstarActionName = (typeof VONSTAR_ACTION_NAMES)[number];
export type VonstarMode = 'execute' | 'plan_only' | 'unavailable';
export type VonstarValidation = 'live_verified' | 'mapped_capture';

export interface VonstarActionDefinition {
  label: string;
  description: string;
  validation: VonstarValidation;
  validationNote: string;
}

export const VONSTAR_ACTIONS: Readonly<Record<VonstarActionName, VonstarActionDefinition>> = {
  lock_all: {
    label: 'Lock All',
    description: 'lock every door',
    validation: 'mapped_capture',
    validationNote: 'Lock All is capture-mapped but has not yet been independently replay-tested.',
  },
  unlock_front: {
    label: 'Unlock Front',
    description: 'unlock the front doors',
    validation: 'live_verified',
    validationNote: '',
  },
  unlock_cargo: {
    label: 'Unlock Cargo',
    description: 'unlock the cargo doors',
    validation: 'mapped_capture',
    validationNote:
      'Unlock Cargo is capture-mapped but has not yet been independently replay-tested.',
  },
};

export interface VonstarActionResult {
  kind: 'action';
  action: VonstarActionName;
  label: string;
  ok: boolean;
  startedAt?: string;
  completedAt?: string;
  error?: string;
}

export interface VonstarLockDomains {
  state: string;
  quality: string;
  frontLocked: boolean | null;
  cargoLocked: boolean | null;
}

export interface VonstarDoorState {
  locked: boolean | null;
  ajar: boolean | null;
  reportedClosed: boolean | null;
  physicalStateObservable: boolean | null;
  lockQuality: string;
  ajarQuality: string;
}

export interface VonstarAccessState {
  observedAt: string;
  complete: boolean;
  lockDomains: VonstarLockDomains;
  doors: Readonly<Record<'driver' | 'passenger' | 'sliding' | 'rear', VonstarDoorState>>;
  wake: {
    count: 0 | 1;
    restoredPassiveAfterSampling: boolean;
  };
  limitations: string[];
}

export interface VonstarAccessResult {
  kind: 'access_state';
  operation: 'access_state';
  ok: boolean;
  accessState?: VonstarAccessState;
  startedAt?: string;
  completedAt?: string;
  error?: string;
}

export type VonstarResult = VonstarActionResult | VonstarAccessResult;

export interface VonstarStatus {
  service: 'vonstar';
  available: boolean;
  mode: VonstarMode;
  busy: boolean;
  cooldownSeconds: number | null;
  lastResult: VonstarResult | null;
  error: string | null;
}

export interface VonstarActionMutation {
  message: string;
  result: VonstarActionResult;
}

export interface VonstarAttempt {
  label: string;
  state: 'working' | 'ok' | 'failed';
  error?: string;
}

export interface VonstarPresentation {
  label: string;
  summary: string;
  tone: StatusTone;
}

export interface VonstarController {
  status: VonstarStatus | null;
  accessState: VonstarAccessState | null;
  error: Error | null;
  loading: boolean;
  refreshing: boolean;
  busy: boolean;
  lastAttempt: VonstarAttempt | null;
  refresh: () => Promise<VonstarStatus | null>;
  perform: (action: VonstarActionName) => Promise<boolean>;
  checkAccessState: () => Promise<boolean>;
}
