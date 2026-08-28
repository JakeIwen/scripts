import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiRequestError } from '../../api/client';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import {
  fetchSystemPower,
  probeDashboard,
  requestDashboardRestart as postDashboardRestart,
  requestSystemPower as postSystemPower,
} from './api';
import type { SystemControlPhase, SystemPowerAction, SystemPowerOperation } from './types';

export const SYSTEM_CONTROL_POLL_INTERVAL_MS = 750;
export const DASHBOARD_RESTART_ONLINE_GRACE_MS = 12_000;
export const DASHBOARD_RESTART_TIMEOUT_MS = 45_000;

export type SystemControlNotice = (message: string, tone: 'normal' | 'error') => void;
export type SystemControlConfirmation = (message: string) => boolean;

export interface SystemControlsState {
  running: boolean;
  locked: boolean;
  phase: SystemControlPhase;
  message: string | null;
  error: string | null;
  operation: SystemPowerOperation | null;
  requestPower: (action: SystemPowerAction) => Promise<void>;
  restartDashboard: () => Promise<void>;
}

function messageFrom(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function confirmationForPower(action: SystemPowerAction): string {
  const question = action === 'reboot' ? 'Reboot vanpi now?' : 'Power down vanpi now?';
  return (
    `${question}\n\nAll managed disks will be safely unmounted and verified first. ` +
    'If that fails, vanpi will stay on.'
  );
}

function delay(milliseconds: number, signal: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve(false);
      return;
    }
    const timeout = window.setTimeout(() => {
      signal.removeEventListener('abort', aborted);
      resolve(true);
    }, milliseconds);
    const aborted = () => {
      window.clearTimeout(timeout);
      resolve(false);
    };
    signal.addEventListener('abort', aborted, { once: true });
  });
}

export function useSystemControls(
  notify?: SystemControlNotice,
  confirm: SystemControlConfirmation = (message) => window.confirm(message),
): SystemControlsState {
  const { running, run } = useSingleFlightAction();
  const [phase, setPhase] = useState<SystemControlPhase>('idle');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [operation, setOperation] = useState<SystemPowerOperation | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const fail = useCallback(
    (detail: string) => {
      setPhase('error');
      setError(detail);
      setMessage(null);
      notify?.(detail, 'error');
    },
    [notify],
  );

  const watchPower = useCallback(
    async (action: SystemPowerAction, initial: SystemPowerOperation, signal: AbortSignal) => {
      setOperation(initial);
      if (initial.status === 'error') {
        fail(initial.error ?? 'Power action failed; vanpi stayed on');
        return;
      }
      if (initial.status === 'complete') {
        setPhase('power-complete');
        setMessage('Power preparation completed; waiting for vanpi to leave the network');
        return;
      }
      if (initial.status !== 'running' || initial.action !== action) {
        fail('Power action was not confirmed by authoritative status');
        return;
      }

      setPhase('watching-power');
      while (await delay(SYSTEM_CONTROL_POLL_INTERVAL_MS, signal)) {
        try {
          const next = await fetchSystemPower(signal);
          setOperation(next);
          if (next.status === 'running') continue;
          if (next.status === 'error') {
            fail(next.error ?? 'Power action failed; vanpi stayed on');
            return;
          }
          if (next.status === 'complete') {
            setPhase('power-complete');
            setMessage('Power preparation completed; waiting for vanpi to leave the network');
            return;
          }
          fail('Power action ended without a completion result');
          return;
        } catch {
          // Once accepted, losing the dashboard is the expected reboot/poweroff result.
          setPhase('power-disconnected');
          setMessage(
            action === 'reboot'
              ? 'Dashboard disconnected while vanpi reboots'
              : 'Dashboard disconnected while vanpi powers down',
          );
          return;
        }
      }
    },
    [fail],
  );

  const requestPower = useCallback(
    async (action: SystemPowerAction): Promise<void> => {
      if (!confirm(confirmationForPower(action))) return;
      await run(async () => {
        const controller = new AbortController();
        controllerRef.current?.abort();
        controllerRef.current = controller;
        setPhase('submitting-power');
        setError(null);
        setMessage(action === 'reboot' ? 'Requesting reboot…' : 'Requesting power down…');

        let accepted: SystemPowerOperation;
        try {
          const result = await postSystemPower(action);
          accepted = result.operation;
          setMessage(result.message);
          notify?.(result.message, 'normal');
        } catch (reason) {
          if (reason instanceof ApiRequestError && reason.status < 500) {
            fail(reason.message);
            return;
          }
          try {
            accepted = await fetchSystemPower(controller.signal);
          } catch {
            setPhase('power-disconnected');
            setMessage('Dashboard disconnected after the power request; outcome is unknown');
            return;
          }
        }
        await watchPower(action, accepted, controller.signal);
      });
    },
    [confirm, fail, notify, run, watchPower],
  );

  const restartDashboard = useCallback(async (): Promise<void> => {
    const confirmed = confirm(
      'Restart the dashboard service now?\n\nVanpi and its other services will stay running.',
    );
    if (!confirmed) return;

    await run(async () => {
      const controller = new AbortController();
      controllerRef.current?.abort();
      controllerRef.current = controller;
      setPhase('submitting-restart');
      setError(null);
      setMessage('Scheduling dashboard restart…');
      const startedAt = Date.now();

      try {
        const result = await postDashboardRestart();
        setMessage(result.message);
        notify?.(result.message, 'normal');
      } catch (reason) {
        if (reason instanceof ApiRequestError && reason.status < 500) {
          fail(reason.message);
          return;
        }
        // A transport failure can be the service disappearing after acceptance.
      }

      setPhase('watching-restart');
      let sawOffline = false;
      while (await delay(SYSTEM_CONTROL_POLL_INTERVAL_MS, controller.signal)) {
        const elapsed = Date.now() - startedAt;
        try {
          await probeDashboard(controller.signal);
          if (sawOffline || elapsed >= DASHBOARD_RESTART_ONLINE_GRACE_MS) {
            setPhase('restart-complete');
            setMessage('Dashboard service restarted');
            notify?.('Dashboard service restarted', 'normal');
            return;
          }
        } catch {
          sawOffline = true;
        }
        if (elapsed >= DASHBOARD_RESTART_TIMEOUT_MS) {
          fail('Dashboard did not return after restart');
          return;
        }
      }
    });
  }, [confirm, fail, notify, run]);

  const locked = running || phase === 'power-complete' || phase === 'power-disconnected';
  return {
    running,
    locked,
    phase,
    message,
    error,
    operation,
    requestPower,
    restartDashboard,
  };
}
