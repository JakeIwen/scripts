import { useCallback, useState } from 'react';

import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { discoverUsbPorts, recoverUsb2, startUsbPortAction } from './api';
import type { UsbPort, UsbPortAction, UsbPortState, UsbStatus } from './types';

export type UsbControlNotice = (message: string, tone: 'normal' | 'error') => void;
export type UsbConfirmation = (message: string) => boolean;

interface PendingUsbOperation {
  action: UsbPortAction;
  key: string;
  startedAt: number;
}

export interface UsbControls {
  running: boolean;
  blocked: boolean;
  uncertainOutcome: boolean;
  pendingOperation: PendingUsbOperation | null;
  lastMessage: string | null;
  lastError: string | null;
  discover: () => Promise<void>;
  runPortAction: (port: UsbPort, action: Exclude<UsbPortAction, 'restore'>) => Promise<void>;
  recoverUsb2: () => Promise<void>;
  reconcile: (status: UsbStatus | null) => void;
}

function messageFrom(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function pendingOperation(ports: UsbPortState): PendingUsbOperation | null {
  const operation = ports.operation;
  if (
    operation.status !== 'running' ||
    operation.action === null ||
    operation.key === null ||
    operation.startedAt === null
  ) {
    return null;
  }
  return {
    action: operation.action,
    key: operation.key,
    startedAt: operation.startedAt,
  };
}

export function useUsbControls(
  refreshStatus: () => Promise<UsbStatus | null>,
  notify?: UsbControlNotice,
  confirm: UsbConfirmation = (message) => window.confirm(message),
): UsbControls {
  const { running, run } = useSingleFlightAction();
  const [uncertainOutcome, setUncertainOutcome] = useState(false);
  const [pending, setPending] = useState<PendingUsbOperation | null>(null);
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const reconcile = useCallback((status: UsbStatus | null) => {
    if (!status) return;
    setPending((current) => {
      if (!current) return null;
      const observed = status.ports.operation;
      return observed.startedAt === current.startedAt &&
        observed.key === current.key &&
        observed.action === current.action
        ? null
        : current;
    });
  }, []);

  const perform = useCallback(
    async (request: () => Promise<{ message: string; ports: UsbPortState }>): Promise<void> => {
      await run(async () => {
        setLastError(null);
        let accepted: PendingUsbOperation | null = null;
        let requestFailed = false;
        try {
          const result = await request();
          accepted = pendingOperation(result.ports);
          setPending(accepted);
          setLastMessage(result.message);
          notify?.(result.message, 'normal');
        } catch (reason) {
          requestFailed = true;
          const message = messageFrom(reason);
          setLastError(message);
          notify?.(message, 'error');
        } finally {
          const refreshed = await refreshStatus();
          if (refreshed) {
            setUncertainOutcome(false);
            if (accepted) {
              const observed = refreshed.ports.operation;
              if (
                observed.startedAt === accepted.startedAt &&
                observed.key === accepted.key &&
                observed.action === accepted.action
              ) {
                setPending(null);
              }
            }
          } else if (requestFailed || accepted === null) {
            setUncertainOutcome(true);
          }
        }
      });
    },
    [notify, refreshStatus, run],
  );

  const discover = useCallback(async () => perform(discoverUsbPorts), [perform]);

  const runPortAction = useCallback(
    async (port: UsbPort, action: Exclude<UsbPortAction, 'restore'>): Promise<void> => {
      if ((action === 'off' || action === 'cycle') && port.mountedLabels.length > 0) {
        const message = `Unmount ${port.mountedLabels.join(', ')} before disconnecting this port`;
        setLastError(message);
        notify?.(message, 'error');
        return;
      }
      await perform(() => startUsbPortAction(port.key, action));
    },
    [notify, perform],
  );

  const requestRecovery = useCallback(async (): Promise<void> => {
    const accepted = confirm(
      'Restore the Pi USB 2 bus? Every USB 2 device will disconnect briefly. ' +
        'Recovery refuses to run if USB 2 storage is mounted.',
    );
    if (!accepted) return;
    await perform(recoverUsb2);
  }, [confirm, perform]);

  return {
    running,
    blocked: running || uncertainOutcome || pending !== null,
    uncertainOutcome,
    pendingOperation: pending,
    lastMessage,
    lastError,
    discover,
    runPortAction,
    recoverUsb2: requestRecovery,
    reconcile,
  };
}
