import { useState } from 'react';

import { useUbntControls } from './controls';
import { useUbntWifiStatus } from './hooks';
import type { StarlinkStatusResource } from './StarlinkControl';
import { UbntSheet } from './UbntSheet';
import { UbntTile } from './UbntTile';

export interface UbntFeatureProps {
  onConnectivityChanged?: () => void | Promise<void>;
  dashboardStatus?: StarlinkStatusResource;
}

export function UbntFeature({ onConnectivityChanged, dashboardStatus }: UbntFeatureProps = {}) {
  const [sheetOpen, setSheetOpen] = useState(false);
  const resource = useUbntWifiStatus();
  const controls = useUbntControls(resource, onConnectivityChanged);

  const openSheet = () => {
    setSheetOpen(true);
    void resource.refresh();
  };

  return (
    <>
      <UbntTile
        status={resource.data}
        error={resource.error}
        refreshing={resource.refreshing}
        onOpen={openSheet}
      />
      <UbntSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        resource={resource}
        controls={controls}
        dashboardStatus={dashboardStatus}
      />
    </>
  );
}
