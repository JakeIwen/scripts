import { useCallback } from 'react';

import { useToast } from '../../components/ToastProvider';
import type { PollingState } from '../../hooks/usePollingResource';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { startSpeedtest } from './api';
import type { SpeedtestStatus } from './types';

export interface SpeedtestControl {
  busy: boolean;
  start: () => Promise<void>;
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

export function useSpeedtestControl(resource: PollingState<SpeedtestStatus>): SpeedtestControl {
  const { showToast } = useToast();
  const action = useSingleFlightAction();

  const start = useCallback(async () => {
    await action.run(async () => {
      try {
        const result = await startSpeedtest();
        showToast(result.message);
      } catch (reason) {
        showToast(errorMessage(reason), 'error');
      } finally {
        // A fresh GET resolves success, failure, and lost-response ambiguity.
        // useSpeedtestStatus changes to a one-second interval while it is running.
        await resource.refresh();
      }
    });
  }, [action, resource.refresh, showToast]);

  return { busy: action.running, start };
}
