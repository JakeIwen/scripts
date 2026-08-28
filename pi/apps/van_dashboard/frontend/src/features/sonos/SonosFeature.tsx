import { useState } from 'react';

import { useToast } from '../../components/ToastProvider';
import { useSonosControls } from './controls';
import { useSonosStatus } from './hooks';
import { useSonosTrackProgress } from './progress';
import { SonosSheet } from './SonosSheet';
import { SonosTile } from './SonosTile';

export function SonosFeature() {
  const [sheetOpen, setSheetOpen] = useState(false);
  const resource = useSonosStatus();
  const { showToast } = useToast();
  const controls = useSonosControls(resource.refresh, showToast);
  const progress = useSonosTrackProgress(resource.data?.nowPlaying ?? null, resource.lastUpdatedAt);

  return (
    <>
      <SonosTile
        status={resource.data}
        error={resource.error}
        refreshing={resource.refreshing}
        progress={progress}
        onOpen={() => setSheetOpen(true)}
      />
      {sheetOpen && (
        <SonosSheet
          open
          onClose={() => setSheetOpen(false)}
          status={resource.data}
          error={resource.error}
          refreshing={resource.refreshing}
          progress={progress}
          onRefresh={resource.refresh}
          controls={controls}
        />
      )}
    </>
  );
}
