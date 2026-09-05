import { useCallback } from 'react';

import { useToast } from '../../components/ToastProvider';
import type { PollingState } from '../../hooks/usePollingResource';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import {
  abortUbntOperation,
  connectUbntProfile,
  provisionUbntNetwork,
  resumeUbntAutomaticSelection,
  scanUbntNetworks,
  updateUbntProfile,
} from './api';
import type {
  UbntMutationResult,
  UbntOperationKind,
  UbntProfileUpdate,
  UbntProvisionRequest,
  UbntWifiStatus,
} from './types';

export const UBNT_INITIAL_CONVERGENCE_DELAY_MS = 500;
export const UBNT_CONVERGENCE_INTERVAL_MS = 1_200;

type RefreshUbnt = PollingState<UbntWifiStatus>['refresh'];
type Wait = (milliseconds: number) => Promise<void>;
type Mutation = () => Promise<UbntMutationResult>;
type ConnectivityChanged = () => void | Promise<void>;
type ShowToast = (message: string, tone?: 'normal' | 'error') => void;

export interface UbntControls {
  busy: boolean;
  aborting: boolean;
  abort: () => Promise<boolean>;
  scan: () => Promise<boolean>;
  connect: (profile: string) => Promise<boolean>;
  provision: (request: UbntProvisionRequest) => Promise<boolean>;
  resumeAutomatic: () => Promise<boolean>;
  updateProfile: (update: UbntProfileUpdate) => Promise<boolean>;
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

export async function convergeUbntOperation(
  refresh: RefreshUbnt,
  pause: Wait = wait,
): Promise<UbntWifiStatus | null> {
  await pause(UBNT_INITIAL_CONVERGENCE_DELAY_MS);
  let status = await refresh();

  while (status?.operation.status === 'running') {
    await pause(UBNT_CONVERGENCE_INTERVAL_MS);
    status = await refresh();
  }

  return status;
}

function changesConnectivity(kind: UbntOperationKind | null): boolean {
  return (
    kind === 'connect' ||
    kind === 'provision' ||
    kind === 'update-profile' ||
    kind === 'resume' ||
    kind === 'abort'
  );
}

export async function executeUbntMutation(
  mutation: Mutation,
  refresh: RefreshUbnt,
  showToast: ShowToast,
  onConnectivityChanged?: ConnectivityChanged,
  pause: Wait = wait,
): Promise<boolean> {
  try {
    const accepted = await mutation();
    const status = await convergeUbntOperation(refresh, pause);
    if (status === null) throw new Error('UBNT status refresh failed during convergence');
    if (status.operation.status === 'error') {
      throw new Error(status.operation.error ?? 'UBNT Wi-Fi operation failed');
    }
    if (status.operation.kind === 'abort' && accepted.status.operation.kind !== 'abort') {
      return false;
    }

    showToast(status.operation.message ?? 'UBNT Wi-Fi updated');
    if (changesConnectivity(status.operation.kind)) {
      // Connectivity is a separate status domain. A callback failure must not
      // misreport an already-completed antenna operation as failed.
      try {
        await onConnectivityChanged?.();
      } catch {
        // Its owner will expose any refresh error on its own resource.
      }
    }
    return true;
  } catch (reason) {
    showToast(errorMessage(reason), 'error');
    return false;
  } finally {
    // Resolve lost-response ambiguity without ever retrying the mutation.
    await refresh();
  }
}

/**
 * The optional callback lets an embedding dashboard refresh its separate
 * connectivity domain after antenna routing changes. The standard dashboard's
 * OpenWrt resource already polls independently, so UBNT does not own that state.
 */
export function useUbntControls(
  resource: PollingState<UbntWifiStatus>,
  onConnectivityChanged?: ConnectivityChanged,
): UbntControls {
  const { showToast } = useToast();
  const { running, run } = useSingleFlightAction();
  const { running: aborting, run: runAbort } = useSingleFlightAction();

  const execute = useCallback(
    async (mutation: Mutation): Promise<boolean> => {
      const completed = await run(() =>
        executeUbntMutation(mutation, resource.refresh, showToast, onConnectivityChanged),
      );
      return completed ?? false;
    },
    [onConnectivityChanged, resource.refresh, run, showToast],
  );

  const abort = useCallback(async (): Promise<boolean> => {
    const completed = await runAbort(() =>
      executeUbntMutation(abortUbntOperation, resource.refresh, showToast, onConnectivityChanged),
    );
    return completed ?? false;
  }, [onConnectivityChanged, resource.refresh, runAbort, showToast]);

  return {
    busy: running,
    aborting,
    abort,
    scan: () => execute(scanUbntNetworks),
    connect: (profile) => execute(() => connectUbntProfile(profile)),
    provision: (request) => execute(() => provisionUbntNetwork(request)),
    resumeAutomatic: () => execute(resumeUbntAutomaticSelection),
    updateProfile: (update) => execute(() => updateUbntProfile(update)),
  };
}
