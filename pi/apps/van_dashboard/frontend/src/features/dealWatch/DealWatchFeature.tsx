import { useState } from 'react';

import { useDealWatchControls } from './controls';
import { DealWatchSheet } from './DealWatchSheet';
import { DealWatchTile } from './DealWatchTile';
import { useDealWatchStatus } from './hooks';

export function DealWatchFeature() {
  const [sheetOpen, setSheetOpen] = useState(false);
  const resource = useDealWatchStatus(sheetOpen);
  const controls = useDealWatchControls(resource);

  return (
    <>
      <DealWatchTile
        resource={resource}
        operationBusy={controls.busy}
        onOpen={() => setSheetOpen(true)}
      />
      <DealWatchSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        resource={resource}
        controls={controls}
      />
    </>
  );
}
