import { ApiRequestError } from '../../../api/client';
import { usePollingResource } from '../../../hooks/usePollingResource';
import { useSingleFlightAction } from '../../../hooks/useSingleFlightAction';
import { useCallback, useRef, useState } from 'react';
import { OPENWRT_CLIENT_INTERVAL_MS } from '../hooks';
import { editPolicy, editSSID, fetchMeteredStatus } from './api';
import type { PolicyEdit } from './schema';

export function useMetered(open: boolean) {
  const readGeneration = useRef(0);
  const load = useCallback((signal: AbortSignal) => {
    readGeneration.current += 1;
    return fetchMeteredStatus(signal);
  }, []);
  const resource = usePollingResource({
    load,
    intervalMs: OPENWRT_CLIENT_INTERVAL_MS,
    enabled: open,
  });
  const { running, run } = useSingleFlightAction();
  const [notice, setNotice] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const refreshAfterWrite = async () => {
    const generation = readGeneration.current;
    const refreshed = await resource.refresh();
    // A poll started during the write may still contain the pre-write revision.
    return readGeneration.current === generation ? resource.refresh() : refreshed;
  };

  const mutate = async (write: () => Promise<unknown>): Promise<boolean> => {
    let succeeded = false;
    await run(async () => {
      setNotice(null);
      setFailed(false);
      try {
        await write();
        succeeded = true;
        const refreshed = await refreshAfterWrite();
        setNotice(refreshed ? 'Saved.' : 'Saved, but status could not be refreshed.');
        setFailed(!refreshed);
      } catch (error) {
        const refreshed = await refreshAfterWrite();
        const conflict = error instanceof ApiRequestError && error.status === 409;
        setNotice(
          conflict
            ? `Settings changed elsewhere. ${refreshed ? 'Refetched current settings; review and try again.' : 'Could not refetch; refresh before trying again.'}`
            : `${error instanceof Error ? error.message : String(error)} No automatic retry.`,
        );
        setFailed(true);
      }
    });
    return succeeded;
  };

  const data = resource.data;
  return {
    resource,
    notice,
    failed,
    blocked: !open || running || resource.refreshing || !data || Boolean(resource.error),
    changeSSID: (op: 'add' | 'remove', ssid: string) =>
      data ? mutate(() => editSSID(op, ssid, data.config.revision)) : Promise.resolve(false),
    changePolicy: (edit: PolicyEdit) =>
      data ? mutate(() => editPolicy(edit, data.policy.revision)) : Promise.resolve(false),
  };
}

export type MeteredControls = ReturnType<typeof useMetered>;
