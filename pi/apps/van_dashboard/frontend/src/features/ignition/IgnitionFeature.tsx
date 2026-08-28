import { useCallback, useEffect, useState } from 'react';

import { useIgnitionCountdown } from './countdown';
import { useIgnitionMonitorStatus } from './hooks';
import { IgnitionSheet } from './IgnitionSheet';
import { IgnitionTile } from './IgnitionTile';
import type { IgnitionMonitorStatus } from './types';
import { useIgnitionMonitorActions } from './useIgnitionMonitorActions';

export function IgnitionFeature() {
  const [sheetOpen, setSheetOpen] = useState(false);
  const [authoritativeStatus, setAuthoritativeStatus] = useState<IgnitionMonitorStatus | null>(
    null,
  );
  const actions = useIgnitionMonitorActions({
    onAuthoritativeStatus: setAuthoritativeStatus,
  });
  const resource = useIgnitionMonitorStatus(sheetOpen, !actions.running);

  useEffect(() => {
    if (resource.data) setAuthoritativeStatus(resource.data);
  }, [resource.data]);

  const refresh = useCallback(async () => {
    const refreshed = await resource.refresh();
    if (refreshed) setAuthoritativeStatus(refreshed);
    return refreshed;
  }, [resource.refresh]);

  const remainingSeconds = useIgnitionCountdown(authoritativeStatus?.monitor.deadline ?? null);

  return (
    <>
      <IgnitionTile
        status={authoritativeStatus}
        error={resource.error}
        refreshing={resource.refreshing}
        remainingSeconds={remainingSeconds}
        onOpen={() => setSheetOpen(true)}
      />
      <IgnitionSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        status={authoritativeStatus}
        error={resource.error}
        refreshing={resource.refreshing}
        remainingSeconds={remainingSeconds}
        onRefresh={refresh}
        actions={actions}
      />
    </>
  );
}
