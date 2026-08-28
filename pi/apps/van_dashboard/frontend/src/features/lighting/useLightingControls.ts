import { useCallback } from 'react';

import { useToast } from '../../components/ToastProvider';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import {
  fetchLightingStatus,
  setLightBrightness,
  setLightColorTemperature,
  setLightHue,
  setLightingPower,
} from './api';
import type { LightingControlActions, LightingMutationResult, LightingStatus } from './types';

interface LightingControlsOptions {
  status: LightingStatus | null;
  onAuthoritativeStatus: (status: LightingStatus) => void;
}

function errorValue(reason: unknown): Error {
  return reason instanceof Error ? reason : new Error(String(reason));
}

/**
 * Serialize lighting mutations and always replace optimistic UI with a
 * backend-decoded snapshot. This hook deliberately owns no optimistic state.
 */
export function useLightingControls({
  status,
  onAuthoritativeStatus,
}: LightingControlsOptions): LightingControlActions {
  const { running, run } = useSingleFlightAction();
  const { showToast } = useToast();

  const recoverAfterFailure = useCallback(async () => {
    try {
      const refreshed = await fetchLightingStatus(new AbortController().signal);
      onAuthoritativeStatus(refreshed);
    } catch {
      // Preserve the mutation error as the useful message. The normal poller
      // resumes after this action and will continue reconciliation.
    }
  }, [onAuthoritativeStatus]);

  const execute = useCallback(
    async (mutation: () => Promise<LightingMutationResult>): Promise<boolean> => {
      const completed = await run(async () => {
        try {
          const result = await mutation();
          onAuthoritativeStatus(result.lighting);
          showToast(result.message);
          return true;
        } catch (reason) {
          const error = errorValue(reason);
          await recoverAfterFailure();
          showToast(error.message, 'error');
          throw error;
        }
      });
      return completed === true;
    },
    [onAuthoritativeStatus, recoverAfterFailure, run, showToast],
  );

  const setPower = useCallback(
    (target: string, enabled: boolean) => execute(() => setLightingPower(target, enabled)),
    [execute],
  );
  const setBrightness = useCallback(
    (entityId: string, brightness: number) =>
      execute(() => setLightBrightness(entityId, brightness)),
    [execute],
  );
  const setHue = useCallback(
    (entityId: string, hue: number) => execute(() => setLightHue(entityId, hue)),
    [execute],
  );
  const setColorTemperature = useCallback(
    (entityId: string, kelvin: number) => execute(() => setLightColorTemperature(entityId, kelvin)),
    [execute],
  );

  const setGroupBrightness = useCallback(
    async (groupId: string, brightness: number): Promise<boolean> => {
      const group = status?.groups.find((candidate) => candidate.id === groupId);
      const entities =
        group?.lights.filter((light) => light.available).map((light) => light.entityId) ?? [];

      const completed = await run(async () => {
        if (!group || entities.length === 0) {
          const error = new Error('No available lights in this room');
          showToast(error.message, 'error');
          throw error;
        }

        let lastReturnedStatus: LightingStatus | null = null;
        let mutationError: Error | null = null;
        try {
          for (const entityId of entities) {
            const result = await setLightBrightness(entityId, brightness);
            lastReturnedStatus = result.lighting;
          }
        } catch (reason) {
          mutationError = errorValue(reason);
        }

        let refreshError: Error | null = null;
        try {
          const refreshed = await fetchLightingStatus(new AbortController().signal);
          onAuthoritativeStatus(refreshed);
        } catch (reason) {
          refreshError = errorValue(reason);
          if (lastReturnedStatus) onAuthoritativeStatus(lastReturnedStatus);
        }

        if (mutationError) {
          showToast(mutationError.message, 'error');
          throw mutationError;
        }
        if (refreshError) {
          const error = new Error(
            `${group.label} brightness changed, but status refresh failed: ${refreshError.message}`,
          );
          showToast(error.message, 'error');
          throw error;
        }

        showToast(`${group.label} brightness set to ${brightness}%`);
        return true;
      });
      return completed === true;
    },
    [onAuthoritativeStatus, run, showToast, status],
  );

  return {
    running,
    setPower,
    setBrightness,
    setHue,
    setColorTemperature,
    setGroupBrightness,
  };
}
