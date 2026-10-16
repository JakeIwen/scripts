import { useState } from 'react';

import { ApiRequestError } from '../../api/client';
import { usePollingResource } from '../../hooks/usePollingResource';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { fetchAlarmFavorites, fetchAlarms, type SonosAlarm } from './alarms';

/** The feature owns writes so closing a view cannot release a pending action. */
export function useSonosAlarmControls(sheetOpen: boolean) {
  const [expanded, setExpanded] = useState(false);
  const { running, run } = useSingleFlightAction();
  const visible = sheetOpen && expanded;
  const resource = usePollingResource({
    load: fetchAlarms,
    intervalMs: 60_000,
    enabled: visible && !running,
  });
  const favorites = usePollingResource({
    load: fetchAlarmFavorites,
    intervalMs: 300_000,
    enabled: visible,
  });
  const [editing, setEditing] = useState<{ alarm: SonosAlarm | null } | null>(null);
  const [message, setMessage] = useState('');
  const [failure, setFailure] = useState(false);

  async function perform(action: () => Promise<{ message: string }>) {
    await run(async () => {
      setMessage('');
      setFailure(false);
      try {
        const result = await action();
        setMessage(result.message);
        setEditing(null);
      } catch (error) {
        setFailure(true);
        setMessage(error instanceof Error ? error.message : 'Alarm change failed');
        // Validation rejects before writing. Keep that draft for correction;
        // uncertain or stale writes must be reconciled before reopening it.
        if (!(error instanceof ApiRequestError && error.status === 400)) setEditing(null);
      } finally {
        await resource.refresh();
      }
    });
  }

  return {
    expanded,
    setExpanded,
    resource,
    favorites,
    editing,
    setEditing,
    message,
    failure,
    busy: running || resource.refreshing,
    perform,
  };
}

export type SonosAlarmControls = ReturnType<typeof useSonosAlarmControls>;
