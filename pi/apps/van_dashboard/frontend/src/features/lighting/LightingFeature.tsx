import { useCallback, useEffect, useState } from 'react';

import { useLightingStatus } from './hooks';
import { LightingSheet } from './LightingSheet';
import { LightingTile } from './LightingTile';
import type { LightingStatus } from './types';
import { useLightingControls } from './useLightingControls';

export function LightingFeature() {
  const [sheetOpen, setSheetOpen] = useState(false);
  const [authoritativeStatus, setAuthoritativeStatus] = useState<LightingStatus | null>(null);
  const controls = useLightingControls({
    status: authoritativeStatus,
    onAuthoritativeStatus: setAuthoritativeStatus,
  });
  const resource = useLightingStatus(sheetOpen, !controls.running);

  useEffect(() => {
    if (resource.data) setAuthoritativeStatus(resource.data);
  }, [resource.data]);

  const refresh = useCallback(async () => {
    const refreshed = await resource.refresh();
    if (refreshed) setAuthoritativeStatus(refreshed);
    return refreshed;
  }, [resource.refresh]);

  return (
    <>
      <LightingTile
        status={authoritativeStatus}
        error={resource.error}
        refreshing={resource.refreshing}
        onOpen={() => setSheetOpen(true)}
        controls={controls}
      />
      <LightingSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        status={authoritativeStatus}
        error={resource.error}
        refreshing={resource.refreshing}
        onRefresh={refresh}
        controls={controls}
      />
    </>
  );
}
