import { useCallback, useState } from 'react';

import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import {
  controlSonosTransport,
  selectSonosCoordinator,
  setSonosGrouping,
  setSonosGroupMuted,
  setSonosGroupVolume,
  setSonosSpeakerMuted,
  setSonosSpeakerVolume,
} from './api';
import type { SonosMutationResult, SonosStatus, SonosTransportAction } from './types';

export type SonosControlNotice = (message: string, tone: 'normal' | 'error') => void;

export interface SonosControls {
  running: boolean;
  activeAction: string | null;
  lastMessage: string | null;
  lastError: string | null;
  transport: (action: SonosTransportAction) => Promise<SonosMutationResult | null>;
  selectCoordinator: (name: string) => Promise<SonosMutationResult | null>;
  setGrouping: (name: string, grouped: boolean) => Promise<SonosMutationResult | null>;
  setSpeakerVolume: (name: string, volume: number) => Promise<SonosMutationResult | null>;
  setSpeakerMuted: (name: string, muted: boolean) => Promise<SonosMutationResult | null>;
  setGroupVolume: (volume: number) => Promise<SonosMutationResult | null>;
  setGroupMuted: (muted: boolean) => Promise<SonosMutationResult | null>;
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

/**
 * Serialize every Sonos mutation and reconcile with GET /api/speakers in
 * `finally`. A failed POST has an ambiguous physical outcome and is never
 * retried automatically.
 */
export function useSonosControls(
  refreshStatus: () => Promise<SonosStatus | null>,
  notify?: SonosControlNotice,
): SonosControls {
  const { running, run } = useSingleFlightAction();
  const [activeAction, setActiveAction] = useState<string | null>(null);
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const perform = useCallback(
    async (
      actionLabel: string,
      request: () => Promise<SonosMutationResult>,
    ): Promise<SonosMutationResult | null> => {
      return run(async () => {
        setActiveAction(actionLabel);
        setLastError(null);
        let mutationError: string | null = null;
        try {
          const result = await request();
          setLastMessage(result.message);
          notify?.(result.message, 'normal');
          return result;
        } catch (reason) {
          mutationError = errorMessage(reason);
          setLastError(mutationError);
          notify?.(mutationError, 'error');
          return null;
        } finally {
          try {
            await refreshStatus();
          } catch (reason) {
            const refreshError = `Could not refresh Sonos status: ${errorMessage(reason)}`;
            if (mutationError === null) setLastError(refreshError);
            notify?.(refreshError, 'error');
          } finally {
            setActiveAction(null);
          }
        }
      });
    },
    [notify, refreshStatus, run],
  );

  const transport = useCallback(
    (action: SonosTransportAction) =>
      perform(`transport:${action}`, () => controlSonosTransport(action)),
    [perform],
  );
  const selectCoordinator = useCallback(
    (name: string) => perform(`select:${name}`, () => selectSonosCoordinator(name)),
    [perform],
  );
  const setGrouping = useCallback(
    (name: string, grouped: boolean) =>
      perform(`group:${name}`, () => setSonosGrouping(name, grouped)),
    [perform],
  );
  const setSpeakerVolume = useCallback(
    (name: string, volume: number) =>
      perform(`volume:${name}`, () => setSonosSpeakerVolume(name, volume)),
    [perform],
  );
  const setSpeakerMuted = useCallback(
    (name: string, muted: boolean) =>
      perform(`mute:${name}`, () => setSonosSpeakerMuted(name, muted)),
    [perform],
  );
  const setGroupVolume = useCallback(
    (volume: number) => perform('group-volume', () => setSonosGroupVolume(volume)),
    [perform],
  );
  const setGroupMuted = useCallback(
    (muted: boolean) => perform('group-mute', () => setSonosGroupMuted(muted)),
    [perform],
  );

  return {
    running,
    activeAction,
    lastMessage,
    lastError,
    transport,
    selectCoordinator,
    setGrouping,
    setSpeakerVolume,
    setSpeakerMuted,
    setGroupVolume,
    setGroupMuted,
  };
}
