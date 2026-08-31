import { useState } from 'react';

import { useVonstarController } from './hooks';
import { VonstarSheet } from './VonstarSheet';
import { VonstarTile } from './VonstarTile';

export function VonstarFeature() {
  const [sheetOpen, setSheetOpen] = useState(false);
  const controller = useVonstarController();
  return (
    <>
      <VonstarTile controller={controller} onOpen={() => setSheetOpen(true)} />
      <VonstarSheet open={sheetOpen} onClose={() => setSheetOpen(false)} controller={controller} />
    </>
  );
}
